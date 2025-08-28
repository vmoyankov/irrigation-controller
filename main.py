# main.py
import sys
import uasyncio as asyncio
from machine import Pin
import aiorepl

import config
import utils
import logic
import webapp
import net


def main():
    button = Pin(config.BUTTON_PIN, Pin.IN)
    if not button.value():
        utils.log("INFO", "Button pressed. Exiting")
        return

    # Restore persistent values and settings
    logic.restore_persistent_data()
    logic.load_settings()
    logic.load_last_message()
    logic.current_state.set(logic.State.IDLE)

    # Start background tasks
    asyncio.create_task(aiorepl.task())
    utils.log("INFO", "asyncio REPL started")

    asyncio.create_task(webapp.app.serve())
    utils.log("INFO", f"Web server started on port {config.WEB_SERVER_PORT}.")

    asyncio.create_task(logic.watchdog())
    asyncio.create_task(net.connect_wifi())
    asyncio.create_task(net.sync_time())
    asyncio.create_task(logic.scheduler())

    asyncio.run_until_complete()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        utils.log("INFO", "Program stopped by user.")
    except Exception as e:
        utils.log("CRITICAL", f"A critical error occurred in main: {e}")
        sys.print_exception(e)
    finally:
        logic.pump_stop()
        logic.open_valve(0)
        logic.current_state.off()
        utils.log("INFO", "System halted. Cleanup complete. Watchdog will restart in 10 secs.")


