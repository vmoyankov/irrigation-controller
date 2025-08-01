import time
import network
from machine import Pin, PWM, Timer
import micropython
import array

import config

BUS_PINS = (21, 20, 10, 7)
PUMP_PIN = 5
METER_PIN = 6
PWM_FREQ = 100
PUMP_DUTY = 20000
FEEDBACK_PIN = 0

LED_PIN = 8

PULSES_PER_L = 3250

class Meter:

    def __init__(self, in_pin, feedback_pin, log_size=300):
        self.counter = 0
        self.log_size = log_size
        self.log = array.array('L', (0 for x in range(log_size)))
        self.pin = Pin(in_pin, Pin.IN)
        self.feedback = Pin(feedback_pin, Pin.OUT, value=0)
        self.pin.irq(handler=self.cb, trigger=Pin.IRQ_RISING | Pin.IRQ_FALLING)
        self.last_tick = time.ticks_us()
        self.state = True
        self.fast_irq = 0


    def cb(self,pin):
        now = time.ticks_us()
        dt = now - self.last_tick
        self.last_tick = now
        if dt < 1000: 
            self.fast_irq += 1
            return
        self.feedback.value(self.state)
        if self.counter < self.log_size:
            self.log[self.counter] = dt
        self.counter += 1
        self.state = not self.state

    def __repr__(self):
        return self.counter


class Blink:

    def __init__(self, pin, timer=0, f=None):
        self.led = Pin(pin, Pin.OUT, value=0)
        self.state = False
        self.timer = Timer(timer)
        self.freq(f)

    def cb(self, timer):
        self.state = not self.state
        self.led.value(self.state)

    def freq(self, f):
        if f is not None:
            self.timer.deinit()
            self.timer.init(period = 500 // f, 
            mode=Timer.PERIODIC,
            callback=self.cb
            )

    def stop(self):
        self.timer.deinit()
        self.led.off()


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


bus = []
#pump = Pin(PUMP_PIN, Pin.OUT, value=0)
pump = PWM(Pin(PUMP_PIN), freq=PWM_FREQ, duty_u16=0)
led = Blink(LED_PIN)
meter = Meter(METER_PIN, FEEDBACK_PIN)

def init():
    global bus

    micropython.alloc_emergency_exception_buf(100)
    for pin_id in BUS_PINS:
        bus.append(Pin(pin_id, Pin.IN))


def open_valve(valve_id):
    print("open valve ", valve_id)
    for i, pin in enumerate(bus):
        lvl = valves[valve_id][i]
        if lvl == 0:
            pin.init(mode=Pin.OUT, value=0)
            print("pin ", pin, " 0")
        elif lvl == 1:
            pin.init(mode=Pin.OUT, value=1)
            print("pin ", pin, " 1")
        else:
            pin.init(mode=Pin.IN)
            print("pin ", pin, " -")


def pump_start():
    #pump.on()
    pump.duty_u16(PUMP_DUTY)


def pump_stop():
    #pump.off()
    pump.duty_u16(0)


def do_connect(timeout=30_000):
    import ntptime

    led.freq(2)
    wlan = network.WLAN()
    wlan.active(True)
    if not wlan.isconnected():
        print("connecting to network...")
        wlan.connect(config.SSID, config.WLAN_KEY)
        while not wlan.isconnected():
            led.freq(5)
            time.sleep_ms(100)
            timeout -= 100
            if timeout <= 0:
                print("Timeout connecting to network")
                break
    print("network config:", wlan.ifconfig())
    if wlan.isconnected():
        led.freq(1)
        try:
            ntptime.settime()
        except OSError:
            pass
    else:
        led.freq(2)


def test_meter(ml, valve, timeout=30_000):
    pulses = int(ml * PULSES_PER_L / 1000)
    open_valve(valve)
    time.sleep(0.1)
    start_cnt = meter.counter
    start_time = time.ticks_ms()
    pump_start()
    print("Initial counter:", start_cnt)
    while (meter.counter < start_cnt + pulses and 
           time.ticks_ms() < start_time + timeout) :
        time.sleep_ms(10)
    end_cnt = meter.counter
    end_time = time.ticks_ms()
    open_valve(0)
    pump_stop()
    duration = end_time - start_time 
    print("Stop counter: ", end_cnt, " duration: ", duration)
    print("Fast IRQs:", meter.fast_irq)


init()
do_connect()
