
import time


def _is_dst(lt):
    """
    Returns True if lt is between last Sunday of March and October
    """

    def before_last_sun(mday, wday):
        prev_sun = mday - ((wday + 1) % 7) # 0 - Sun
        next_sun = prev_sun + 7
        # print("Sunday, prev: %d, next: %d" % (prev_sun, next_sun))
        if next_sun <= 31:
            return True
        return False

    (year, month, mday, hour, minute, second, wday, yday) = lt[:8]
    if month > 3 and month < 10:
        return True
    if month == 3: # March
        if before_last_sun(mday, wday):
            return False
        return True
    if month == 10: # Oct
        if before_last_sun(mday, wday):
            return True
        return False
    return False


def _utc_offset(offset=2):

    if _is_dst(time.localtime()):
        return (offset + 1) * 3600
    return offset * 3600


def localtime(tm=None):
    if tm is None:
        tm = time.time()
    return time.localtime(tm + _utc_offset())


def mktime(lt):
    return time.mktime(lt) - _utc_offset()



