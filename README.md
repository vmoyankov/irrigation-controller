### ESP32 MicroPython Irrigation Controller

An open-source, Wi‑Fi enabled irrigation controller for up to 12 zones, built on ESP32‑C6 and MicroPython. It controls a pump and a diode-matrix of valves, schedules daily watering, serves a lightweight web UI, and persists settings and meter data in NVS.

---

### Features
- **12 zones** via a 4‑wire valve matrix
- **Pump PWM control** with configurable power and ramp‑up
- **Flow meter input** with pulse‑counting and timeout safeguards
- **Daily scheduler** with auto‑run window and min interval
- **Web UI** for status, manual start/stop, and configuration
- **REST endpoints** for status and settings
- **NVS persistence** for settings, meter count, and last run message
- **Async REPL** for debugging over serial and network
- **Status LEDs** (RGB + blink) indicate current state

**Note** The daily schedule will not run if the clock is not valid. It can work without WLAN and NTP, as long as the date is after 2025-01-01. The controller will try to reconnect and sync time periodically.

---

### Hardware
- MCU: ESP32‑C6. This version depends on machine.Counter for the flow meter. This hardware is 
not supported by all ESP32 MCU. If not available, it can be replaces with IRQ, but this is 
very sensitive to interference. 
- Outputs:
  - `PUMP_PIN` (PWM)
  - `VALVE_BUS_PINS` (4 pins for diode‑matrix valve selection)
- Inputs:
  - `METER_PIN` (flow meter pulses)
  - `BUTTON_PIN` (hold at boot to skip app)
- Indicators:
  - `RGB_PIN` (NeoPixel)
  - `LED_PIN` (blinking status)

---

### Firmware & Requirements
- MicroPython for ESP32/ESP32‑C3 v.1.26 (older versions will also work without machine.Counter)
- Python 3 on your workstation
- Deployment tools: `mpremote`

Third‑party MicroPython libs are bundled in `lib/` (`aiorepl.py`, `tz.py`, `web.py`).

---

### Quick Start
1) Flash MicroPython to your board (ESP32 or ESP32‑C3).

2) Clone this repo locally.

3) Choose and edit configuration:
   - For ESP32: edit `config.py`
   - For ESP32‑C3: use `config-c3.py` (copy/rename to `config.py`)
   - Set Wi‑Fi credentials (`SSID`, `WLAN_KEY`) and check pin assignments.

4) Upload files to the board (examples use `mpremote`):

```bash
mpremote connect auto fs mkdir /lib || true
mpremote connect auto fs cp lib/aiorepl.py :lib/
mpremote connect auto fs cp lib/tz.py :lib/
mpremote connect auto fs cp lib/web.py :lib/
mpremote connect auto fs cp logic.py main.py net.py utils.py webapp.py config.py :
mpremote connect auto fs mkdir /static || true
mpremote connect auto fs cp -r static/* :static/
mpremote connect auto soft-reset
```

Note

---

### Web UI
- Navigate to `http://<device-ip>/` for the dashboard
- Buttons: Config, Start Now, Stop
- Status updates automatically

Static assets live in `static/`. You can minify them with `minify.sh` (uses online minifiers).

---

### REST API

- `GET /status` → device status

```json
{
  "current-time": "2025-04-01 21:20:15",
  "state": "IDLE",
  "tank": "84.3 L",
  "last-run": "2025-03-31 21:30:02",
  "next-run": "21:30",
  "last-msg": "Cycle completed ...",
  "log": ["..."]
}
```

- `POST /run` → start an irrigation cycle (uses current `settings.volumes`)
- `POST /stop` → cancel active cycle
- `GET /config` → current settings
- `POST /config` → update settings (JSON, validated)

Settings schema:

```json
{
  "volumes": {"1": 750, "2": 800, "3": 1000, "4": 650, "5": 300, "6": 900, "7": 300, "8": 550, "9": null, "10": null, "11": null, "12": null},
  "pumpPower": 30,
  "schedule": {"hour": 21, "minute": 30},
  "autorun": true
}
```

Notes:
- `volumes["n"]` is milliliters for valve `n` (50–3000 ml, or null/0 to skip)
- `pumpPower` is 10–100 (%)
- Scheduler runs once per day within a small window after the configured time and enforces a minimum period between runs. If the RTC year is before 2025 (time not yet synced), the scheduler will not run.

Device maintenance:
- `POST /reset-tank` → zero the stored water meter count
- `POST /restart` → reboot the MCU

---

### Status Indicators

The RGB LED color and the blink LED frequency reflect the current state:

| State       | Color (R,G,B) | Blink (Hz) |
|-------------|----------------|------------|
| BOOTING     | (0, 64, 128)  | 10         |
| CONNECTING  | (128, 128, 0) | 3          |
| IDLE        | (0, 0, 128)   | 1          |
| RUNNING     | (0, 128, 0)   | 2          |
| ERROR       | (128, 0, 0)   | 5          |

Hold the button (`BUTTON_PIN`) during boot to skip starting the app.

---

### Configuration Reference
Edit `config.py` (or start from `config-c3.py` for ESP32‑C3):
- Wi‑Fi: `SSID`, `WLAN_KEY`, `WIFI_TIMEOUT`
- Pins: `BUTTON_PIN`, `LED_PIN`, `RGB_PIN`, `PUMP_PIN`, `METER_PIN`, `VALVE_BUS_PINS`
- Operation: `PUMP_PWM_FREQ`, `PUMP_RAMP_UP_TIME_S`, `PULSES_PER_LITER`, `MIN_FLOW_S_PER_L`, `TANK_SIZE`
- Web: `WEB_SERVER_PORT`
- Defaults: `DEFAULT_SETTINGS` (used on first boot or when NVS empty)

Non‑volatile storage (NVS) keys used: `settings` (blob), `cnt` (meter pulses), `last_run` (epoch), `last_msg` (blob).

---

### Safety & Notes
- Verify valve matrix wiring matches `logic.valves` mapping.
- Test each valve with small volumes first.
- Pump power and flow meter constants must match your hardware.
- Network credentials in `config.py` are in plain text on the device.

---

### Development
- Async REPL runs in background (`aiorepl.task()`); attach over USB or webrepl/webrepl_cli for live inspection.
- Logs are timestamped; before NTP sync, monotonic ticks are used.
- Static assets can be minified with `./minify.sh <file>`.

---

### Contributing
Issues and pull requests are welcome. Please describe your hardware setup (board, pins, valves, flow meter).

---

### License
MIT License. See `LICENSE` for details.
SPDX-License-Identifier: MIT
