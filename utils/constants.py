import pandas as pd

CSV_FILE = "sensortile/logs/csv/sensor_data.csv"
CSV_HEADERS = ["timestamp", "yaw", "pitch", "roll", "vafe"]
SAVE_LOGS = False

ROLL_MIN_AMPLITUDE = 20
NOD_MIN_AMPLITUDE = 70
NOD_TIME_WINDOW = 1.5   # seconds
NOD_COOLDOWN = pd.Timedelta(seconds=2.0)

SERVICE_UUID = "00000000-0004-11e1-9ab4-0002a5d5c51b"
CHARACTERISTIC_01 = "00000001-0004-11e1-ac36-0002a5d5c51b"  # Notify
CHARACTERISTIC_02 = "00000002-0004-11e1-ac36-0002a5d5c51b"  # Notify + Write