import time
import network
from machine import Pin, PWM, Timer

import config

BUS_PINS = (4, 3, 6, 1)
PUMP_PIN = 0
METER_PIN = 5
PWM_FREQ = 100
PUMP_DUTY = 20000

LED_PIN = 8

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
pump: PWM = None
water_counter = 0
meter: Pin = None
led: Pin = None


def init():
    global bus, pump, meter, water_counter, led

    for pin_id in BUS_PINS:
        bus.append(Pin(pin_id, Pin.IN))

    # pump = Pin(PUMP_PIN, Pin.OUT)
    pump = PWM(Pin(PUMP_PIN), freq=PWM_FREQ, duty_u16=0)

    meter = Pin(METER_PIN, Pin.IN)
    meter.irq(handler=meter_callback, trigger=Pin.IRQ_RISING)

    led = Pin(LED_PIN, Pin.OUT, value=1)


def meter_callback(pin):
    global water_counter

    if pin == meter:
        water_counter += 1


def led_blink(freq):
    global led

    t = Timer(0)
    t.init(period = 500 // freq, 
           mode=Timer.PERIODIC,
           callback=lambda t:
                led.value(1 - led.value())
           )



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
    # pump.on()
    pump.duty_u16(PUMP_DUTY)


def pump_stop():
    # pump.off()
    pump.duty_u16(0)


def test_valves():
    for v in (0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12):
        open_valve(v)
        time.sleep(1)
    open_valve(0)


def do_connect(timeout=30_000):
    led_blink(5)
    wlan = network.WLAN()
    wlan.active(True)
    if not wlan.isconnected():
        print("connecting to network...")
        wlan.connect(config.SSID, config.WLAN_KEY)
        while not wlan.isconnected():
            led_blink(5)
            time.sleep_ms(100)
            timeout -= 100
            if timeout <= 0:
                print("Timeout connecting to network")
                break
    print("network config:", wlan.ifconfig())
    if wlan.isconnected():
        led_blink(1)
    else:
        led_blink(2)


def test_meter(amount=1000, timeout=30_000):
    pump_start()
    time.sleep(1)
    start_cnt = water_counter
    start_time = time.ticks_ms()
    print("Initial counter:", water_counter)
    open_valve(1)
    while (water_counter < start_cnt + amount and 
           time.ticks_ms() < start_time + timeout) :
        time.sleep_ms(10)
    open_valve(0)
    pump_stop()
    time.sleep_ms(100)
    print("Stop counter:", water_counter)


init()
do_connect()
