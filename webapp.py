import uasyncio as asyncio
import json
import machine

from tz import localtime

import web
import logic
import config
import utils


app = web.App(host='0.0.0.0', port=config.WEB_SERVER_PORT)


app.static("/static/", "/static")
app.static("/", "/static/index.html")

@app.route("/status")
async def status(r, w):
    tank = config.TANK_SIZE - logic.meter.value() / config.PULSES_PER_LITER
    tank = round(tank, 1)
    hour = logic.settings["schedule"]["hour"]
    minute = logic.settings["schedule"]["minute"]
    st = {
        "current-time": utils.fmt_time(localtime()),
        "state": logic.current_state.text(),
        "tank": tank,
        "last-run": utils.fmt_time(localtime(logic.last_run)),
        "next-run": f"{hour:02d}:{minute:02d}" if logic.settings.get("autorun", True) else "Disabled",
        "last-msg": logic.last_run_msg,
        "log": [logic.error_message],
    }
    await w.awrite(b"HTTP/1.0 200 OK\r\nContent-type: application/json\r\n\r\n")
    await w.awrite(json.dumps(st).encode())


@app.route('/run', methods=['POST'])
async def run_cycle_request(r, w):
    utils.log("INFO", "Run cycle triggered via web interface.")
    if logic.start_cycle_task():
        msg, code = "<div class='status-success'>Cycle started</div>", 200
    else:
        msg, code = "<div class='status-error'>System is not idle, cannot start cycle.</div>", 409
    await w.awrite(f"HTTP/1.0 {code} OK\r\nRefresh: 3;url=/\r\nContent-Type: text/html\r\n\r\n".encode("utf8"))
    await w.awrite(msg.encode("utf8"))


@app.route('/stop', methods=['POST'])
async def stop_cycle_request(r, w):
    utils.log("INFO", "Stop current cycle via web interface.")
    if logic.stop_cycle_task():
        msg, code = "<div class='status-success'>Cycle canceled</div>", 200
    else:
        msg, code = "<div class='status-error'>No active cycle</div>", 409
    await w.awrite(f"HTTP/1.0 {code} OK\r\nRefresh: 3;url=/\r\nContent-Type: text/html\r\n\r\n".encode("utf8"))
    await w.awrite(msg.encode("utf8"))


@app.route('/config', methods=['GET'])
async def get_config(r, w):
    logic.load_settings()
    await w.awrite(b"HTTP/1.0 200 OK\r\nContent-type: application/json\r\n\r\n")
    await w.awrite(json.dumps(logic.settings).encode())


@app.route('/config', methods=['POST'])
async def post_config(r, w):
    buf = await r.read(1024)
    try:
        s = json.loads(buf)
        if not logic.validate_settings(s):
            utils.log("WARNING", f"Settings updated with bad value - {s}. Ignore")
            raise ValueError
        # mutate settings in-place to keep references
        logic.settings.clear()
        logic.settings.update(s)
        utils.log("INFO", "Settings updated from web")
        logic.save_settings()
        await w.awrite(b"HTTP/1.0 200 OK\r\n\r\n")
    except ValueError:
        utils.log("ERROR", f"Bad settings request from web {buf}")
        await w.awrite(b"HTTP/1.0 400 Bad Request\r\n\r\n")


@app.route("/reset-tank", methods=['POST'])
async def reset_tank(r, w):
    utils.log("INFO", "Tank level reset from web")
    logic.meter.value(0)
    logic.nvs.set_i32("cnt", 0)
    logic.nvs.commit()
    await w.awrite(b"HTTP/1.0 200 OK\r\n\r\n")


@app.route('/restart', methods=['POST'])
async def post_restart(r, w):
    machine.reset()


