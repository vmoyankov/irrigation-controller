import uasyncio as asyncio
import network

from tz import localtime

import config
from utils import log
import logic


async def connect_wifi():
    wlan = network.WLAN()
    wlan.active(True)
    while True:
        log("DEBUG", "connect_wifi()")
        try:
            if not wlan.isconnected():
                log("INFO", f"Connecting to network: {config.SSID}")
                wlan.connect(config.SSID, config.WLAN_KEY)
                # Poll for connection with sleep intervals
                for _ in range(int(config.WIFI_TIMEOUT / 100)):
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
            log("ERROR", f"connect_wifi() Exception {e}. Restarting")
            await asyncio.sleep(3)


async def sync_time():
    import ntptime
    while True:
        log("DEBUG", "sync_time()")
        try:
            if logic.current_state.get() == logic.State.IDLE:
                ntptime.settime()
                log("INFO", f"Time set via NTP: {localtime()}. Next sync after 900 sec")
                await asyncio.sleep(900)
        except Exception as e:
            log("WARN", f"Could not set time via NTP: {e}. Retry in 10 sec")
        await asyncio.sleep(10)


