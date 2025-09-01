import sys
import uasyncio as asyncio
import time
from machine import Pin, PWM, WDT
import micropython
from esp32 import NVS
import json
from neopixel import NeoPixel

# --- Third-party libraries ---
from tz import localtime, mktime
from utils import fmt_time, log

# --- Project modules ---
import config

# Allocate buffer for micropython to handle exceptions in IRQs
micropython.alloc_emergency_exception_buf(100)

try:
    from machine import Counter  # type: ignore
except AttributeError:
    class Counter:
        """dummy counter to allow tests"""
        def __init__(self, *args, **kwargs):
            self.value_ = 0

        def value(self, value=None):
            if value is not None:
                oldv = self.value_
                self.value_ = value
                return oldv
            return self.value_


# --- Module-wide state ---
error_message = ""
last_run = 0
last_run_msg = ""
status_message = ""
log_msg = ""


class AsyncBlink:
    """Manages the status LED using an asyncio task."""
    def __init__(self, pin):
        self.led = Pin(pin, Pin.OUT, value=0)
        self.task = None
        self.val = True

    async def _run(self, period_ms):
        while True:
            self.led.value(self.val)
            self.val = not self.val
            await asyncio.sleep_ms(period_ms)

    def freq(self, f):
        self.stop()
        if f is not None and f > 0:
            period_ms = int(1000 // (f * 2))
            self.task = asyncio.create_task(self._run(period_ms))

    def stop(self):
        if self.task:
            self.task.cancel()
            self.task = None
        self.led.off()


class State:
    (
        BOOTING,
        CONNECTING,
        IDLE,
        RUNNING,
        ERROR,
    ) = range(5)

    _STATE_NAMES = {
        BOOTING: "BOOTING",
        CONNECTING: "CONNECTING",
        IDLE: "IDLE",
        RUNNING: "RUNNING",
        ERROR: "ERROR",
    }
    _COLORS = {
        BOOTING: (0, 64, 128),
        CONNECTING: (128, 128, 0),
        IDLE: (0, 0, 128),
        RUNNING: (0, 128, 0),
        ERROR: (128, 0, 0),
    }
    _BLINK_FREQ = {
        BOOTING: 10,
        CONNECTING: 3,
        IDLE: 1,
        RUNNING: 2,
        ERROR: 5,
    }

    def __init__(self, state=BOOTING):
        self.led = NeoPixel(Pin(config.RGB_PIN), 1)
        self.led2 = AsyncBlink(config.LED_PIN)
        self.set(state)

    def set(self, state):
        self.state = state
        self.led[0] = self._COLORS.get(state, (32, 32, 32))
        self.led.write()
        self.led2.freq(self._BLINK_FREQ.get(state, 0.5))
        log("DEBUG", f"State = {self.text()}")

    def get(self):
        return self.state

    def text(self):
        return self._STATE_NAMES.get(self.state, "UNKNOWN")

    def off(self):
        """call this when main loop exits"""
        self.state = None
        self.led[0] = (0, 0, 0)
        self.led.write()
        self.led2.stop()


# --- Valve Matrix Definition ---
valves = (
    (None, None, None, None),  # 0, All valves are closed
    (1, 0, None, None),  # 1
    (1, None, 0, None),  # 2
    (1, None, None, 0),  # 3
    (0, 1, None, None),  # 4
    (None, 1, 0, None),  # 5
    (None, 1, None, 0),  # 6
    (0, None, 1, None),  # 7
    (None, 0, 1, None),  # 8
    (None, None, 1, 0),  # 9
    (0, None, None, 1),  # 10
    (None, 0, None, 1),  # 11
    (None, None, 0, 1),  # 12
)


# --- Global Object Instantiation ---
pump = PWM(Pin(config.PUMP_PIN), freq=config.PUMP_PWM_FREQ, duty=0)
meter = Counter(0, Pin(config.METER_PIN, Pin.IN), filter_ns=1_000_000)
valve_bus_pins = [Pin(x, Pin.IN) for x in config.VALVE_BUS_PINS]
task_cycle = None
nvs = NVS("ic")
settings = config.DEFAULT_SETTINGS
rgb = NeoPixel(Pin(config.RGB_PIN), 1)
current_state = State()


# --- Utility Functions ---

def load_settings():
    """Load settings from NVS, or from config file"""
    global settings
    buf = bytearray(1024)
    try:
        nvs.get_blob('settings', buf)
        settings = json.loads(buf)
        log("INFO", "Settings loaded from NVS")
    except OSError:
        settings = config.DEFAULT_SETTINGS
        log("INFO", "Settings loaded with DEFAULT values from Flash")


def save_settings():
    """Save settings into NVS"""
    global settings
    buf = json.dumps(settings).encode()
    nvs.set_blob("settings", buf)
    nvs.commit()
    log("INFO", f"Settings stored into NVS, {len(buf)} bytes")


def validate_settings(s):
    try:
        for k, v in s["volumes"].items():
            k = int(k)
            if k < 1 or k > 12:
                return False
            if v is None or v == 0:
                continue
            if v < 50 or v > 3000:
                return False
        if s["pumpPower"] < 10 or s["pumpPower"] > 100:
            return False
        hh = s["schedule"]["hour"]
        mm = s["schedule"]["minute"]
        if hh < 0 or hh > 23 or mm < 0 or mm > 59:
            return False
    except KeyError:
        return False
    return True


def load_last_message():
    global last_run_msg
    buf = bytearray(254)
    try:
        n = nvs.get_blob('last_msg', buf)
        last_run_msg = buf[:n].decode()
    except OSError:
        log("INFO", "No last run message saved")


def restore_persistent_data():
    """Restore meter count and last_run from NVS."""
    global last_run
    try:
        stored_counter = nvs.get_i32("cnt")
        meter.value(stored_counter)
        log("INFO", f"Water meter restored to {stored_counter}")
        last_run = nvs.get_i32("last_run")
        log("INFO", f"last_run loaded: {last_run}")
    except OSError:
        log("WARNING", "Stored value for meter not found!")


# --- Core Logic Functions ---
def open_valve(valve_id):
    """Sets the valve bus pins to open a specific valve."""
    log("DEBUG", f"Setting valve matrix for valve_id: {valve_id}")
    for i, pin in enumerate(valve_bus_pins):
        lvl = valves[valve_id][i]
        if lvl is not None:
            pin.init(mode=Pin.OUT, value=lvl)
        else:
            pin.init(mode=Pin.IN)


def pump_start():
    log("INFO", "Pump START")
    pump.duty(settings.get("pumpPower", 50) * 1023 // 100)


def pump_stop():
    log("INFO", "Pump STOP")
    pump.duty(0)


async def valve_ml(valve, ml):
    global error_message, status_message

    if ml is None or ml == 0:
        log("INFO", f"Zero amount for valve {valve}")
        return

    timeout_ms = ml * config.MIN_FLOW_S_PER_L
    pulses_needed = int(ml * config.PULSES_PER_LITER / 1000)
    status_message = f"Dispensing {ml}ml from valve {valve} ({pulses_needed} pulses)"
    log("INFO", status_message)

    start_cnt = meter.value()
    start_time = time.ticks_ms()
    open_valve(valve)

    while meter.value() < start_cnt + pulses_needed:
        if time.ticks_diff(time.ticks_ms(), start_time) > timeout_ms:
            log("WARN", f"  Timeout dispensing from valve {valve}")
            break
        await asyncio.sleep_ms(10)

    duration = time.ticks_diff(time.ticks_ms(), start_time)
    pulses_dispensed = meter.value() - start_cnt
    log("INFO", f"  -> Closed valve {valve}. Dispensed {pulses_dispensed} pulses in {duration/1000:.1f}s.")


async def run_cycle(program):
    """Runs a full irrigation cycle based on the 'program' dictionary."""
    global error_message, last_run_msg, last_run, status_message
    if current_state.get() != State.IDLE:
        log("WARN", "Cannot start cycle, system is not idle.")
        return

    current_state.set(State.RUNNING)
    log("INFO", "--- Starting Irrigation Cycle ---")
    last_run = time.time()
    nvs.set_i32("last_run", last_run)
    nvs.commit()

    open_valve(0)
    pump_start()
    await asyncio.sleep(config.PUMP_RAMP_UP_TIME_S)

    start_cnt = meter.value()
    start_time = time.ticks_ms()
    try:
        for v, ml in sorted(program.items(), key=lambda x: int(x[0])):
            v = int(v)
            await valve_ml(v, ml)

        end_cnt = meter.value()
        end_time = time.ticks_ms()
        duration = time.ticks_diff(end_time, start_time)
        total_water = (end_cnt - start_cnt) / config.PULSES_PER_LITER
        lt = fmt_time(localtime())
        last_run_msg = f"Cycle completed successfully at [{lt}]. Total Time: {duration / 1000:.2f}s Total Water: {total_water:.3f}L"
        status_message = ""
        log("INFO", last_run_msg)
    except Exception as e:
        last_run_msg = f"Cycle failed: {e}"
        log("ERROR", last_run_msg)
        current_state.set(State.ERROR)
    finally:
        log("INFO", "Cycle cleanup: closing all valves and stopping pump.")
        open_valve(0)
        await asyncio.sleep_ms(500)
        pump_stop()
        nvs.set_i32("cnt", meter.value())
        nvs.set_blob("last_msg", last_run_msg)
        nvs.commit()
        log("INFO", "Water meter saved in NVS.")
        if current_state.get() != State.ERROR:
            current_state.set(State.IDLE)


async def watchdog():
    wdt = WDT(timeout=10000)
    while True:
        wdt.feed()
        await asyncio.sleep(1)


async def scheduler():
    global last_run, task_cycle

    def should_run(hr, min_, window=1800, min_period=12 * 60 * 60):
        now = time.time()
        lt = localtime(now)
        target_time = mktime((lt[0], lt[1], lt[2], hr, min_, 0, 0, 0))

        if lt[0] < 2025:  # we don't know the real time; cancel
            log("WARNING", f"Time is incorrect: {fmt_time(lt)}. Reject the scheduler")
            return False

        log("DEBUG", f"Scheduler: now={now}, target={target_time}, diff={now - target_time}, last run {now - last_run} s ago")
        if now >= target_time and now - target_time < window:
            if now - last_run > min_period:
                return True
        return False

    log("INFO", "Scheduler started")
    while True:
        hour = settings["schedule"]["hour"]
        minute = settings["schedule"]["minute"]
        now = time.time()
        lt = localtime(now)

        try:
            if settings.get("autorun", True) and should_run(hour, minute):
                log("INFO", "Scheduler: ready to run taks")
                if current_state.get() == State.IDLE:
                    program = settings["volumes"]
                    task_cycle = asyncio.create_task(run_cycle(program))
                    log("INFO", f"Scheduler: task started at {fmt_time(lt)}")
                else:
                    log("WARNING", f"Scheduler: not IDLE: {current_state.text()}")
        except Exception as e:
            sys.print_exception(e)

        await asyncio.sleep(60 - lt[5])

def start_cycle_task():
    global task_cycle
    if current_state.get() == State.IDLE:
        program = settings["volumes"]
        task_cycle = asyncio.create_task(run_cycle(program))
        return True
    return False


def stop_cycle_task():
    global task_cycle
    if current_state.get() == State.RUNNING and isinstance(task_cycle, asyncio.Task):
        task_cycle.cancel()
        return True
    return False




