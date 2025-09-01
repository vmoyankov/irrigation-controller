# config.py
# Central configuration file for the async irrigation controller.

# --- WiFi Configuration ---
# IMPORTANT: Replace with your network details
SSID = "Your_SSID"
WLAN_KEY = "Your_Password"
WIFI_TIMEOUT = 30000     # max connection time in ms

# --- Hardware Pin Assignments ---
# Define the GPIO pin numbers connected to your hardware.
BUTTON_PIN = 9
LED_PIN = 15
RGB_PIN = 8
PUMP_PIN = 14
METER_PIN = 20
# MONITOR_PIN = 0  # For monitoring meter IRQ with a logic analyzer/scope
#VALVE_BUS_PINS = (21, 20, 10, 7) # The 4 pins controlling the valve matrix
VALVE_BUS_PINS = (3, 2, 1, 0) # The 4 pins controlling the valve matrix

# --- Operational Parameters ---
# Fine-tune the system's behavior.
PUMP_PWM_FREQ = 2000
PUMP_RAMP_UP_TIME_S = 2.0   # Seconds to wait for pump to build pressure
PULSES_PER_LITER = 1700     # Pulses from the flow meter that equal 1 liter
MIN_FLOW_S_PER_L = 240      # Max seconds per liter before a timeout occurs
TANK_SIZE = 85              # Liters

# --- Web Server Configuration ---
WEB_SERVER_PORT = 80
DEFAULT_SETTINGS = {
    "volumes": {
        1: 750,  # 1
        2: 800,  # 2
        3: 1000, # 3
        4: 650,  # 4
        5: 300,  # 5
        6: 900,  # 6
        7: 300,  # 7
        8: 550,  # 8
        9: None, # 9
        10: None, # 10
        11: None, # 11
        12: None, # 12
    },
    "pumpPower": 30,
    "schedule": {
        "hour": 21,
        "minute": 30,
    },
    "autorun": True,
}
