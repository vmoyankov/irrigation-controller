import uasyncio as asyncio
import json
import machine

from tz import localtime

import web
import logic
import config
import utils


app = web.App(host='0.0.0.0', port=config.WEB_SERVER_PORT)


BUF_LEN = 256
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
async def static(r, w):
    filename = "/static/" + r.query
    if r.query.endswith(b'.html'):
        mime = b'text/html'
    else:
        mime = b'text/plain'
    await serve_file(r, w, filename, mime)


@app.route("/status")
async def status(r, w):
    tank = config.TANK_SIZE - logic.meter.value() / config.PULSES_PER_LITER
    tank = round(tank, 1)
    hour = logic.settings["schedule"]["hour"]
    minute = logic.settings["schedule"]["minute"]
    st = {
        "current_time": utils.fmt_time(localtime()),
        "state": logic.current_state.text(),
        "tank": tank,
        "last_run": utils.fmt_time(localtime(logic.last_run)),
        "next_run": f"{hour:02d}:{minute:02d}" if logic.settings.get("autorun", True) else "Disabled",
        "last_msg": logic.last_run_msg,
        "log": [logic.error_message],
    }

    await w.awrite(b"HTTP/1.0 200 OK\r\nContent-type: application/json\r\n\r\n")
    await w.awrite(json.dumps(st).encode())
    await w.drain()


@app.route('/')
async def index(r, w):
    await serve_file(r, w, "/static/main.html", b"text/html")


@app.route('/config.html')
async def config_html(r, w):
    await serve_file(r, w, "/static/config.html", b"text/html")


@app.route('/min.css')
async def min_css(r, w):
    await serve_file(r, w, "/static/min.css", b"text/css")


@app.route('/run', methods=['POST'])
async def run_cycle_request(r, w):
    utils.log("INFO", "Run cycle triggered via web interface.")
    if logic.start_cycle_task():
        msg, code = "Cycle started", 200
    else:
        msg, code = "System is not idle, cannot start cycle.", 409
    w.write(f"HTTP/1.0 {code} OK\r\nRefresh: 3;url=/\r\nContent-Type: text/html\r\n\r\n".encode("utf8"))
    html = f"""<html><head></head><body><h1>{msg}</h1></body></html>"""
    w.write(html.encode("utf8"))
    await w.drain()


@app.route('/stop', methods=['POST'])
async def stop_cycle_request(r, w):
    utils.log("INFO", "Stop current cycle via web interface.")
    if logic.stop_cycle_task():
        msg, code = "Cycle canceled", 200
    else:
        msg, code = "No task active", 409
    w.write(f"HTTP/1.0 {code} OK\r\nRefresh: 3;url=/\r\nContent-Type: text/html\r\n\r\n".encode("utf8"))
    html = f"""<html><head></head><body><h1>{msg}</h1></body></html>"""
    w.write(html.encode("utf8"))
    await w.drain()


@app.route('/config', methods=['GET'])
async def get_config(r, w):
    logic.load_settings()
    await w.awrite(b"HTTP/1.0 200 OK\r\nContent-type: application/json\r\n\r\n")
    await w.awrite(json.dumps(logic.settings).encode())
    await w.drain()


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
        w.write(b"HTTP/1.0 200 OK\r\n\r\n")
    except ValueError:
        utils.log("ERROR", f"Bad settings request from web {buf}")
        w.write(b"HTTP/1.0 400 Bad Request\r\n\r\n")
    await w.drain()


@app.route("/reset-tank", methods=['POST'])
async def reset_tank(r, w):
    utils.log("INFO", "Tank level reset from web")
    logic.meter.value(0)
    logic.nvs.set_i32("cnt", 0)
    logic.nvs.commit()
    w.write(b"HTTP/1.0 200 OK\r\n\r\n")
    await w.drain()


@app.route('/restart', methods=['POST'])
async def post_restart(r, w):
    machine.reset()


