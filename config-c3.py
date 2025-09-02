# config.py
# Central configuration file for the irrigation controller.

# --- WiFi Configuration ---
# IMPORTANT: Replace with your network details
SSID = "SSID"
WLAN_KEY = "PASSWORD"
WIFI_TIMEOUT = 30000     # max connection time in ms

# --- Hardware Pin Assignments ---
# Define the GPIO pin numbers connected to your hardware.
BUTTON_PIN = 9
LED_PIN = 8
RGB_PIN = 5
PUMP_PIN = 4
METER_PIN = 20
# MONITOR_PIN = 0  # For monitoring meter IRQ with a logic analyzer/scope
VALVE_BUS_PINS = (21, 20, 10, 7) # The 4 pins controlling the valve matrix

# --- Operational Parameters ---
# Fine-tune the system's behavior.
PUMP_PWM_FREQ = 2000
PUMP_RAMP_UP_TIME_S = 2.0   # Seconds to wait for pump to build pressure
PULSES_PER_LITER = 1700     # Pulses from the flow meter that equal 1 liter
MIN_FLOW_S_PER_L = 240      # Max seconds per liter before a timeout occurs
TANK_SIZE = 85

# --- Web Server Configuration ---
WEB_SERVER_PORT = 80

# --- Default settings ---
# This is used on first boot or when non-volatile storage is empty
DEFAULT_SETTINGS = {
    "volumes": {
        "1": 750,
        "2": 800,
        "3": 1000,
        "4": 650,
        "5": 300,
        "6": 900,
        "7": 300,
        "8": 550,
        "9": None,
        "10": None,
        "11": None,
        "12": None,
    },
    "pumpPower": 30,
    "schedule": {
        "hour": 21,
        "minute": 30,
    },
    "autorun": True,
}
