# main.py
import os
import uasyncio as asyncio
import time
import network
from machine import Pin, PWM, WDT
import micropython
from esp32 import NVS

# --- Third-party libraries ---
# https://github.com/wybiral/micropython-aioweb
import web
from tz import localtime

# --- Project modules ---
import config  # Our new configuration file
from program import program

# Allocate buffer for micropython to handle exceptions in IRQs
micropython.alloc_emergency_exception_buf(100)

# --- State Management ---
STATE_BOOTING, STATE_CONNECTING_WIFI, STATE_IDLE, STATE_RUNNING_CYCLE, STATE_ERROR = range(5)
STATE_NAMES = {
    STATE_BOOTING: "BOOTING", STATE_CONNECTING_WIFI: "CONNECTING",
    STATE_IDLE: "IDLE", STATE_RUNNING_CYCLE: "RUNNING", STATE_ERROR: "ERROR"
}
current_state = STATE_BOOTING
error_message = ""
last_run = 0
last_run_msg = "Never"
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

class Meter:
    """Handles the flow meter using a hardware interrupt for precision."""
    def __init__(self, in_pin, monitor_pin):
        self.counter = 0
        self.fast_irq = 0
        self.pin = Pin(in_pin, Pin.IN)
        self.monitor = Pin(monitor_pin, Pin.OUT, value=0)
        self.last_tick = time.ticks_us()
        self.monitor_state = True
        self.pin.irq(handler=self.cb, trigger=Pin.IRQ_RISING)

    def cb(self, pin):
        """Interrupt Service Routine: Must be lean and fast."""
        now = time.ticks_us()
        if time.ticks_diff(now, self.last_tick) < 1000: # Debounce
            self.fast_irq += 1
            return
        self.last_tick = now
        self.counter += 1
        self.monitor.value(self.monitor_state) # Toggle monitor pin for debugging
        self.monitor_state = not self.monitor_state

    def set(self, value):
        self.counter = value

    def __repr__(self):
        return str(self.counter)

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
led = AsyncBlink(config.LED_PIN)
pump = PWM(Pin(config.PUMP_PIN), freq=config.PUMP_PWM_FREQ, duty_u16=0)
meter = Meter(config.METER_PIN, config.MONITOR_PIN)
valve_bus_pins = [ Pin(x, Pin.IN) for x in config.VALVE_BUS_PINS ]
task_cycle = None
nvs = NVS("ic")
app = web.App(host='0.0.0.0', port=config.WEB_SERVER_PORT )
wdt = None


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
    pump.duty_u16(config.PUMP_DUTY_CYCLE)

def pump_stop():
    log("INFO", "Pump STOP")
    pump.duty_u16(0)

async def do_connect():
    """Connects to WiFi, non-blocking."""
    global current_state
    led.freq(5)
    wlan = network.WLAN()
    wlan.active(True)
    if not wlan.isconnected():
        log("INFO", f"Connecting to network: {config.SSID}")
        wlan.connect(config.SSID, config.WLAN_KEY)
        start_time = time.ticks_ms()
        while not wlan.isconnected():
            if time.ticks_diff(time.ticks_ms(), start_time) > config.WIFI_TIMEOUT:
                led.freq(2)
                log("ERROR", "WiFi connection timed out.")
                return False
            await asyncio.sleep_ms(100)
    
    log("INFO", f"WiFi connected. IP: {wlan.ifconfig()[0]}")
    led.freq(1)
    try:
        import ntptime
        ntptime.settime()
        log("INFO", f"Time set via NTP: {localtime()}")
    except Exception as e:
        log("WARN", f"Could not set time via NTP: {e}")
    return True

async def valve_ml(valve, ml):
    global error_message, status_message

    """Dispenses a specific amount of water from a valve."""
    timeout_ms = ml * config.MIN_FLOW_S_PER_L
    pulses_needed = int(ml * config.PULSES_PER_LITER / 1000)
    status_message = f"Dispensing {ml}ml from valve {valve} ({pulses_needed} pulses)"
    log("INFO", status_message)
    
    start_cnt = meter.counter
    start_time = time.ticks_ms()
    open_valve(valve)
    
    while (meter.counter < start_cnt + pulses_needed):
        if time.ticks_diff(time.ticks_ms(), start_time) > timeout_ms:
            log("WARN", f"  Timeout dispensing from valve {valve}")
            break
        await asyncio.sleep_ms(10)
    
    duration = time.ticks_diff(time.ticks_ms(), start_time)
    pulses_dispensed = meter.counter - start_cnt
    log("INFO", f"  -> Closed valve {valve}. Dispensed {pulses_dispensed} pulses in {duration/1000:.1f}s.")


async def run_cycle(program):
    """Runs a full irrigation cycle based on the 'program' dictionary."""
    global current_state, error_message, last_run_msg, last_run, status_message
    if current_state != STATE_IDLE:
        log("WARN", "Cannot start cycle, system is not idle.")
        return

    current_state = STATE_RUNNING_CYCLE
    log("INFO", "--- Starting Irrigation Cycle ---")
    
    open_valve(0)
    pump_start()
    await asyncio.sleep(config.PUMP_RAMP_UP_TIME_S)

    start_cnt = meter.counter
    start_time = time.ticks_ms()
    try:
        for v, ml in sorted(program.items()):
            await valve_ml(v, ml)

        end_cnt = meter.counter
        end_time = time.ticks_ms()
        duration = time.ticks_diff(end_time, start_time)
        total_water = (end_cnt - start_cnt) / config.PULSES_PER_LITER
        last_run = time.time()
        lt = fmt_time(localtime(last_run))
        last_run_msg = f"Cycle completed successfully at [{lt}]. Total Time: {duration / 1000:.2f}s Total Water: {total_water:.3f}L"
        nvs.set_i32("last_run", last_run)
        status_message = ""
        log("INFO", last_run_msg)
    except Exception as e:
        error_message = f"Cycle failed: {e}"
        log("ERROR", error_message)
        current_state = STATE_ERROR
    finally:
        log("INFO", "Cycle cleanup: closing all valves and stopping pump.")
        open_valve(0)
        await asyncio.sleep_ms(500) # Give valves time to close
        pump_stop()
        nvs.set_i32("cnt", meter.counter)
        nvs.commit()
        log("INFO", "Water meter saved in NVS.")
        if current_state != STATE_ERROR:
            current_state = STATE_IDLE

async def watchdog():
    global wdt

    while True:
        wdt.feed()
        await asyncio.sleep(1)

# --- Web Server Routes ---


BUF_LEN=256
buf = bytearray(BUF_LEN)

async def serve_file(r, w, filename, mime=b"text/html"):

    try:
        with open(filename, "rb") as f:
            await w.awrite(b"HTTP/1.0 200 OK\r\nContent-type: " + mime + "\r\n\r\n")
            while True:
                n = f.readinto(buf)
                if n == 0:
                    break
                await w.awrite(buf[:n])
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


@app.route('/')
async def index(r,w):
    """Main status page for the web interface."""
    status_name = STATE_NAMES.get(current_state, "UNKNOWN")
    liters = meter.counter / config.PULSES_PER_LITER
    html = f"""
    <!DOCTYPE html><html><head><title>Irrigation Controller</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>body{{font-family:sans-serif;}}</style></head><body>
    <h1>Irrigation Controller</h1>
    <p><strong>Current_time:</strong> {fmt_time(localtime())}</p>
    <p><strong>State:</strong> {status_name}</p>
    <p>{status_message}</p>
    <p><strong>Last Irrigation:</strong> {fmt_time(localtime(last_run))}: {last_run_msg}</p>
    <p><strong>Water Meter:</strong> {liters:.1f}L ({meter.counter} pulses)</p>
    <p><strong>Last Error:</strong> {error_message or "None"}</p>
    <p><string><a href="/program">Program</a></p>
    <p><a href="/static?config.html">Config</a></p>
    <form action="/run" method="post">
        <button type="submit" style="padding:10px;">Run Irrigation Cycle</button>
    </form>
    <form action="/stop" method="post">
        <button type="submit" style="padding:10px;">Stop</button>
    </form>
    <p>Last message: <pre>{log_msg}</pre></p>
    </body></html>
    """

    w.write(b"HTTP/1.0 200 OK\r\nRefresh: 5\r\nContent-Type: text/html\r\n\r\n")
    w.write(html.encode("utf8"))
    await w.drain()

@app.route('/run', methods=['POST'])
async def run_cycle_request(r,w):
    """Triggers the irrigation cycle via a POST request."""

    global task_cycle

    log("INFO", "Run cycle triggered via web interface.")
    if current_state == STATE_IDLE:
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
    if current_state == STATE_RUNNING_CYCLE and isinstance(task_cycle, asyncio.Task):
        task_cycle.cancel()
        msg, code = "Cycle canceled", 200
    else:
        msg, code = "No task active", 409
    w.write(f"HTTP/1.0 {code} OK\r\nRefresh: 3;url=/\r\nContent-Type: text/html\r\n\r\n".encode("utf8"))
    html = f"""<html><head></head><body><h1>{msg}</h1></body></html>"""
    w.write(html.encode("utf8"))
    await w.drain()


@app.route('/program')
async def print_program(r,w):
    html = str(program)
    w.write(b"HTTP/1.0 200 OK\r\nContent-Type: text/plain\r\n\r\n")
    w.write(html.encode("utf8"))
    await w.drain()

# --- Main Application Logic ---
async def main():
    global current_state, error_message, last_run, wdt
    
    # Start watchdog
    wdt = WDT(timeout=10000)
    asyncio.create_task(watchdog())

    # load meter from NV storage:
    try:
        stored_counter = nvs.get_i32("cnt")
        meter.set(stored_counter)
        last_run = nvs.get_i32("last_run")
        log("INFO", f"Water meter restored to {stored_counter}")
    except OSError:
        log("WARNING", "Stored value for meter not found!")
        pass

    # Start the web server as a background task
    try:
        asyncio.create_task(app.serve())
        log("INFO", "Web server started on port {config.WEB_SERVER_PORT}.")
    except Exception as e:
        log("ERROR", f"Failed to start web server: {e}")

    try:
        import aiorepl
        repl = asyncio.create_task(aiorepl.task())
        log("INFO", "asyncio REPL started")
    except Exception as e:
        log("ERROR", f"Failed to start asyncio repl: {e}")

    while True:
        state_name = STATE_NAMES.get(current_state, "UNKNOWN")
        log("DEBUG", f"Main loop. Current state: {state_name}")

        if current_state == STATE_BOOTING:
            current_state = STATE_CONNECTING_WIFI
        
        elif current_state == STATE_CONNECTING_WIFI:
            if await do_connect():
                current_state = STATE_IDLE
            else:
                error_message = "Failed to connect to WiFi."
                current_state = STATE_ERROR
        
        elif current_state == STATE_IDLE:
            led.freq(0.5) # Slow blink for idle
            await asyncio.sleep(60)
            
        elif current_state == STATE_RUNNING_CYCLE:
            led.freq(5) # Fast blink for running
            await asyncio.sleep(1)

        elif current_state == STATE_ERROR:
            led.freq(10) # Very fast "panic" blink
            log("ERROR", f"System in ERROR state: {error_message}")
            await asyncio.sleep(30) # Wait before retrying or halting
        
        else:
            log("WARNING", "Unknown state")
            await asyncio.sleep(1)


# --- Program Entry Point ---
if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log("INFO", "Program stopped by user.")
    except Exception as e:
        log("CRITICAL", f"A critical error occurred in main: {e}")
    finally:
        pump_stop()
        open_valve(0)
        led.stop()
        log("INFO", "System halted. Cleanup complete.")
