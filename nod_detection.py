import pandas as pd
from utils.constants import NOD_MIN_AMPLITUDE, NOD_TIME_WINDOW

def detect_nod(yaw, timestamp, recent_yaw):

    # Update buffer
    recent_yaw.append((timestamp, yaw))
    recent_yaw = [(t, p) for t, p in recent_yaw if t > timestamp - pd.Timedelta(seconds=NOD_TIME_WINDOW)]

    yaw_series = [p for _, p in recent_yaw]
    if len(yaw_series) < 5:
        return False

    # Look for any local extremum (valley or peak)
    for i in range(1, len(yaw_series) - 1):
        p0, p1, p2 = yaw_series[i - 1], yaw_series[i], yaw_series[i + 1]
        if (p1 < p0 and p1 < p2) or (p1 > p0 and p1 > p2):  # local min or max
            delta1 = abs(p1 - p0)
            delta2 = abs(p1 - p2)
            if min(delta1, delta2) >= NOD_MIN_AMPLITUDE:
                recent_yaw.clear()
                return True

    return False
