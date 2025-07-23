CSV_FILE = "logs/csv/sensor_data.csv"
CSV_HEADERS = ["timestamp", "yaw", "pitch", "roll"]
SAVE_LOGS = False
NOD_MIN_AMPLITUDE = 40
NOD_TIME_WINDOW = 2   # seconds
SERVICE_UUID = "00000000-0004-11e1-9ab4-0002a5d5c51b"
CHARACTERISTIC_01 = "00000001-0004-11e1-ac36-0002a5d5c51b"  # Notify
CHARACTERISTIC_02 = "00000002-0004-11e1-ac36-0002a5d5c51b"  # Notify + Write