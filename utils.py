import time
from tz import localtime


def fmt_time(lt):
    return f"{lt[0]:04d}-{lt[1]:02d}-{lt[2]:02d} {lt[3]:02d}:{lt[4]:02d}:{lt[5]:02d}"


def log(level, msg):
    try:
        ts = fmt_time(localtime())
    except TypeError:
        ts = f"{time.ticks_ms()//1000}s"
    print(f"[{ts}] [{level.upper()}] {msg}")


