# main.py
import sys
import uasyncio as asyncio
import time
import network
from machine import Pin, PWM, WDT
import micropython
from esp32 import NVS
import json
from neopixel import NeoPixel
import machine

# --- Third-party libraries ---
# https://github.com/wybiral/micropython-aioweb
import web
from tz import localtime, mktime
import aiorepl

try:
    from machine import Counter
except AttributeError:
    class Counter:
        """ dummy counter to allow tests """
        def __init__(self, *argsi, **kwargs):
            self.value_ = 0

        def value(self, value=None):
            if value is not None:
                oldv = self.value_
                self.value_ = value
                return oldv
            return self.value_


# --- Project modules ---
import config

# Allocate buffer for micropython to handle exceptions in IRQs
micropython.alloc_emergency_exception_buf(100)


error_message = ""
last_run = 0
last_run_msg = ""
status_message = ""
log_msg = ""

def fmt_time(lt):
    return f"{lt[0]:04d}-{lt[1]:02d}-{lt[2]:02d} {lt[3]:02d}:{lt[4]:02d}:{lt[5]:02d}"


def log(level, msg):
    """Simple logging function with timestamps."""
    global log_msg

    try:
        # Format time if NTP has been set
        ts = fmt_time(localtime())
    except TypeError:
        # Fallback to seconds since boot if time not set
        ts = f"{time.ticks_ms()//1000}s"
    log_msg = f"[{ts}] [{level.upper()}] {msg}"
    print(log_msg)

# --- Hardware Abstraction Classes ---

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
# --- State Management ---
    (
        BOOTING, 
        CONNECTING, 
        IDLE, 
        RUNNING, 
        ERROR
    ) = range(5)

    _STATE_NAMES = {
        BOOTING: "BOOTING", 
        CONNECTING: "CONNECTING",
        IDLE: "IDLE", 
        RUNNING: "RUNNING", 
        ERROR: "ERROR"
    }
    _COLORS={
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
        self.led[0] = self._COLORS.get(state, (32,32,32))
        self.led.write()
        self.led2.freq(self._BLINK_FREQ.get(state, 0.5))
        log("DEBUG", f"State = {self.text()}")

    def get(self):
        return self.state

    def text(self):
        return self._STATE_NAMES.get(self.state, "UNKNOWN")

    def off(self):
        """call this when main loog exits"""
        self.state = None
        self.led[0] = (0,0,0)
        self.led.write()
        self.led2.stop()


# --- Valve Matrix Definition (unchanged) ---
valves = (
    (None, None, None, None),  # 0, All valves are closed
    (1   , 0   , None, None),  # 1
    (1   , None, 0   , None),  # 2
    (1   , None, None, 0   ),  # 3
    (0   , 1   , None, None),  # 4
    (None, 1   , 0   , None),  # 5
    (None, 1   , None, 0   ),  # 6
    (0   , None, 1   , None),  # 7
    (None, 0   , 1   , None),  # 8
    (None, None, 1   , 0   ),  # 9
    (0   , None, None, 1   ),  # 10
    (None, 0   , None, 1   ),  # 11
    (None, None, 0   , 1   ),  # 12
)


# --- Global Object Instantiation ---
# Using constants from config.py
pump = PWM(Pin(config.PUMP_PIN), freq=config.PUMP_PWM_FREQ, duty=0)
meter = Counter(0, Pin(config.METER_PIN, Pin.IN), filter_ns=1_000_000)
valve_bus_pins = [ Pin(x, Pin.IN) for x in config.VALVE_BUS_PINS ]
task_cycle = None
nvs = NVS("ic")
app = web.App(host='0.0.0.0', port=config.WEB_SERVER_PORT )
settings = config.DEFAULT_SETTINGS
rgb = NeoPixel(Pin(config.RGB_PIN), 1)
current_state = State()


# #####################################
# --- Core Logic Functions ---
# #####################################

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

    """Dispenses a specific amount of water from a valve."""
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
    
    while (meter.value() < start_cnt + pulses_needed):
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
        for v, ml in sorted(program.items()):
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
        last_run_msg  = f"Cycle failed: {e}"
        log("ERROR", last_run_msg)
        current_state.set(State.ERROR)
    finally:
        log("INFO", "Cycle cleanup: closing all valves and stopping pump.")
        open_valve(0)
        await asyncio.sleep_ms(500) # Give valves time to close
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

    def should_run(hr, min_, window=1800, min_period=12*60*60):
        now = time.time()
        lt = localtime(now)
        target_time = mktime((lt[0], lt[1], lt[2], hr, min_, 0, 0, 0))

        if lt[0] < 2025: # we don't know the real time; cancel
            log("WARNING", f"Time is incorrect: {fmt_time(lt)}. Reject the scheduler")
            return False

        # Allow tolerance window (e.g. 30 mins past)
        log("DEBUG", f"Scheduler: now={now}, target={target_time}, diff={now-target_time}, last run {now-last_run} s ago")
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
            if should_run(hour, minute):
                log("INFO", "Scheduler: ready to run taks")
                if current_state.get() == State.IDLE:
                    program = dict(enumerate(settings["volumes"], start=1))
                    task_cycle = asyncio.create_task(run_cycle(program))
                    log("INFO", f"Scheduler: task started at {fmt_time(lt)}")
                else:
                    log("WARNING", f"Scheduler: not IDLE: {current_state.text()}")
        except Exception as e:
            sys.print_exception(e)

        await asyncio.sleep(60 - lt[5])



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


def load_last_message():
    global last_run_msg 
    buf = bytearray(254)
    try:
        n = nvs.get_blob('last_msg', buf)
        last_run_msg = buf[:n].decode()
    except OSError:
        log("INFO", "No last run message saved")

# #####################################
# --- Web Server Routes ---
# #####################################


BUF_LEN=256
buf = bytearray(BUF_LEN)

async def serve_file(r, w, filename, mime=b"text/html"):

    try:
        with open(filename, "rb") as f:
            await w.awrite(b"HTTP/1.0 200 OK\r\nContent-type: " + mime + b"\r\n\r\n")
            while True:
                n = f.readinto(buf)
                if n == 0:
                    break
                await w.awrite(buf, sz=n)
    except OSError:
        await w.awrite(b'HTTP/1.0 404 Not Found\r\n\r\n')
    finally:
        await w.drain()


@app.route("/static")
async def static(r,w):

    filename = "/static/" + r.query
    if r.query.endswith(b'.html'):
        mime = b'text/html'
    else:
        mime = b'text/plain'
    await serve_file(r, w, filename, mime)


@app.route("/status")
async def status(r,w):
    tank = config.TANK_SIZE - meter.value() / config.PULSES_PER_LITER
    tank = round(tank, 1)
    hour = settings["schedule"]["hour"]
    minute = settings["schedule"]["minute"]
    st = {
        "current_time": fmt_time(localtime()),
        "state": current_state.text(),
        "tank": tank,
        "last_run": fmt_time(localtime(last_run)),
        "next_run": f"{hour:02d}:{minute:02d}",
        "last_msg": last_run_msg,
        "log": [error_message],
    }

    await w.awrite(b"HTTP/1.0 200 OK\r\nContent-type: application/json\r\n\r\n")
    await w.awrite(json.dumps(st).encode())
    await w.drain()


@app.route('/')
async def index(r,w):
    """Main status page for the web interface."""

    await serve_file(r, w, "/static/main.html", b"text/html")


@app.route('/config.html')
async def index(r,w):
    await serve_file(r, w, "/static/config.html", b"text/html")


@app.route('/run', methods=['POST'])
async def run_cycle_request(r,w):
    """Triggers the irrigation cycle via a POST request."""

    global task_cycle

    log("INFO", "Run cycle triggered via web interface.")
    if current_state.get() == State.IDLE:
        program = dict(enumerate(settings["volumes"], start=1))
        task_cycle = asyncio.create_task(run_cycle(program))
        msg, code = "Cycle started", 200
    else:
        msg, code = "System is not idle, cannot start cycle.", 409
    w.write(f"HTTP/1.0 {code} OK\r\nRefresh: 3;url=/\r\nContent-Type: text/html\r\n\r\n".encode("utf8"))
    html = f"""<html><head></head><body><h1>{msg}</h1></body></html>"""
    w.write(html.encode("utf8"))
    await w.drain()


@app.route('/stop', methods=['POST'])
async def stop_cycle_request(r,w):
    """Triggers the irrigation cycle via a POST request."""

    global task_cycle

    log("INFO", "Stop current cycle via web interface.")
    if current_state.get() == State.RUNNING and isinstance(task_cycle, asyncio.Task):
        task_cycle.cancel()
        msg, code = "Cycle canceled", 200
    else:
        msg, code = "No task active", 409
    w.write(f"HTTP/1.0 {code} OK\r\nRefresh: 3;url=/\r\nContent-Type: text/html\r\n\r\n".encode("utf8"))
    html = f"""<html><head></head><body><h1>{msg}</h1></body></html>"""
    w.write(html.encode("utf8"))
    await w.drain()


@app.route('/config', methods=['GET'])
async def get_config(r,w):
    load_settings()
    await w.awrite(b"HTTP/1.0 200 OK\r\nContent-type: application/json\r\n\r\n")
    await w.awrite(json.dumps(settings).encode())
    await w.drain()


@app.route('/config', methods=['POST'])
async def post_config(r,w):
    global settings

    def validate(settings):
        try:
            for v in settings["volumes"]:
                if v is None or v == 0:
                    continue
                if v < 50 or v > 3000:
                    return False
            if settings["pumpPower"] < 10 or settings["pumpPower"] > 100:
                return False
            hh = settings["schedule"]["hour"]
            mm = settings["schedule"]["minute"]
            if hh < 0 or hh > 23 or mm < 0 or mm > 59:
                return False
        except KeyError:
            return False

        return True

    buf = await r.read(1024)
    try:
        s = json.loads(buf)
        if not validate(s):
            log("WARNING", f"Settings updated with bad value - {s}. Ignore")
            raise ValueError
        settings = s
        log("INFO", "Settings updated from web")
        save_settings()
        w.write(b"HTTP/1.0 200 OK\r\n\r\n")
    except ValueError:
        log("ERROR", f"Bad settings request from web {buf}")
        w.write(b"HTTP/1.0 400 Bad Request\r\n\r\n")
    await w.drain()



@app.route("/reset-tank", methods=['POST'])
async def reset_tank(r,w):
    """Reset Tank level"""

    log("INFO", "Tank level reset from web")
    meter.value(0)
    nvs.set_i32("cnt", 0)
    nvs.commit()
    w.write(b"HTTP/1.0 200 OK\r\n\r\n")
    await w.drain()


@app.route('/restart', methods=['POST'])
async def post_restart(r,w):

    machine.reset()

# #####################################
# --- Main Application Logic ---
# #####################################


async def do_connect():
    """Connects to WiFi, async task, never returns."""
    wlan = network.WLAN()
    wlan.active(True)
    while True:
        log("DEBUG", "do_connect()")
        try:
            if not wlan.isconnected():
                log("INFO", f"Connecting to network: {config.SSID}")
                wlan.connect(config.SSID, config.WLAN_KEY)
                start_time = time.ticks_ms()
                while time.ticks_diff(time.ticks_ms(), start_time) < config.WIFI_TIMEOUT:
                    if wlan.isconnected():
                        break
                    await asyncio.sleep_ms(100)
            if wlan.isconnected():
                log("INFO", f"WiFi connected. IP: {wlan.ifconfig()[0]}")
                await asyncio.sleep(600)
            else:
                log("ERROR", "WiFi connection timed out. Retry in 15 sec")
                wlan.active(False)
                await asyncio.sleep(15)
                wlan.active(True)
        except Exception as e:
            log("ERROR", f"do_connect() Exceprion {e}. Restarting")
            await asyncio.sleep(3)


async def sync_clock():
    import ntptime
    while True:
        log("DEBUG", "sync_clock()")
        try:
            if current_state.get() == State.IDLE:
                ntptime.settime()
                log("INFO", f"Time set via NTP: {localtime()}. Next sync after 900 sec")
                await asyncio.sleep(900)
        except Exception as e:
            log("WARN", f"Could not set time via NTP: {e}. Retry in 10 sec")
        await asyncio.sleep(10)


def main():
    global current_state, error_message, last_run
    
    button = Pin(config.BUTTON_PIN, Pin.IN)
    if not button.value():
        log("INFO", "Button pressed. Exiting")
        return

    # Start watchdog
    asyncio.create_task(watchdog())

    # load meter from NV storage:
    try:
        stored_counter = nvs.get_i32("cnt")
        meter.value(stored_counter)
        log("INFO", f"Water meter restored to {stored_counter}")
        last_run = nvs.get_i32("last_run")
        log("INFO", f"last_run loaded: {last_run}")
    except OSError:
        log("WARNING", "Stored value for meter not found!")
        pass

    load_settings()
    load_last_message()

    current_state.set(State.IDLE)
    # Start aio repl
    asyncio.create_task(aiorepl.task())
    log("INFO", "asyncio REPL started")

    # Start the web server as a background task
    asyncio.create_task(app.serve())
    log("INFO", f"Web server started on port {config.WEB_SERVER_PORT}.")

    # Keep trying to (re-)connect to WLAN
    asyncio.create_task(do_connect())

    # Keep clock in sync with NTP
    asyncio.create_task(sync_clock())

    # Start scheduler
    asyncio.create_task(scheduler())

    asyncio.run_until_complete()

# --- Program Entry Point ---
if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("INFO", "Program stopped by user.")
    except Exception as e:
        log("CRITICAL", f"A critical error occurred in main: {e}")
        sys.print_exception(e)
    finally:
        pump_stop()
        open_valve(0)
        current_state.off()
        log("INFO", "System halted. Cleanup complete. Watchdog will restart in 10 secs.")
