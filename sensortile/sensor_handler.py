import pandas as pd
import logging
import os
import struct
from sensortile.nod_detection import detect_nod_up, detect_nod_down
from utils.constants import CSV_HEADERS, NOD_TIME_WINDOW, NOD_MIN_AMPLITUDE, SAVE_LOGS, NOD_COOLDOWN

class SensorTileHandler:
    def __init__(self):
        self.df = pd.DataFrame(columns=CSV_HEADERS)
        self.last_nod_time = None

    def handle_notification(self, sender, data):
        if SAVE_LOGS:
            logging.info(f"\nNotification from {sender}:")
            logging.info(f"Hex: {data.hex()}")
            logging.info(f"Length: {len(data)} bytes")

        if len(data) >= 45:
            try:
                # yaw, pitch, roll = struct.unpack("<fff", data[33:45])
                # vafe = struct.unpack("<f", data[61:65])[0]
                yaw, pitch, roll = struct.unpack("<fff", data[9:21])
            
                # QVAR/vAFE (ECG) at bytes 61-64
                if len(data) >= 65:
                    vafe, = struct.unpack("<f", data[61:65])
                else:
                    vafe = float("nan")
                    
                timestamp = pd.Timestamp.now()

                new_row = {"timestamp": timestamp, "yaw": yaw, "pitch": pitch, "roll": roll, "vafe": vafe}
                self.df = pd.concat([self.df, pd.DataFrame([new_row])], ignore_index=True)
                self.df = self.df[self.df["timestamp"] > timestamp - pd.Timedelta(seconds=NOD_TIME_WINDOW)]

                if SAVE_LOGS:
                    logging.info(f"Head Pose -> Yaw: {yaw:.2f}, Pitch: {pitch:.2f}, Roll: {roll:.2f}, Vafe: {vafe:.2f}")

                if self.last_nod_time is None or (timestamp - self.last_nod_time) > NOD_COOLDOWN:
                    if detect_nod_up(self.df, NOD_MIN_AMPLITUDE):
                        logging.info("Nod up detected!")
                    elif detect_nod_down(self.df, NOD_MIN_AMPLITUDE):
                        logging.info("Nod down detected!")
                    self.last_nod_time = timestamp
            except Exception as e:
                logging.error(f"Error decoding data: {e}")

    def save_log(self, path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.df.to_csv(path, index=False)
        logging.info(f"Saved log to {path}")
