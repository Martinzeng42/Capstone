import pandas as pd
import logging
import os
import struct
from sensortile.movement_detection import detect_nod_up, detect_roll
from utils.constants import CSV_HEADERS, NOD_TIME_WINDOW, NOD_MIN_AMPLITUDE, SAVE_LOGS, NOD_COOLDOWN, ROLL_MIN_AMPLITUDE

class SensorTileHandler:
    def __init__(self):
        self.data = pd.DataFrame(columns=CSV_HEADERS)
        self.object_pos = pd.DataFrame(columns=["yaw", "pitch"])
        self.last_nod_time = None
        self.setup = True

    def handle_notification(self, sender, data):
        if SAVE_LOGS:
            logging.info(f"\nNotification from {sender}:")
            logging.info(f"Hex: {data.hex()}")
            logging.info(f"Length: {len(data)} bytes")

        if len(data) >= 45:
            try:
                yaw, pitch, roll = struct.unpack("<fff", data[33:45])
                vafe = struct.unpack("<f", data[61:65])[0]
                timestamp = pd.Timestamp.now()

                new_row = {"timestamp": timestamp, "yaw": yaw, "pitch": pitch, "roll": roll, "vafe": vafe}
                self.data = pd.concat([self.data, pd.DataFrame([new_row])], ignore_index=True)
                self.data = self.data[self.data["timestamp"] > timestamp - pd.Timedelta(seconds=NOD_TIME_WINDOW)]

                if SAVE_LOGS:
                    logging.info(f"Head Pose -> Yaw: {yaw:.2f}, Pitch: {pitch:.2f}, Roll: {roll:.2f}, Vafe: {vafe:.2f}")

                if self.last_nod_time is None or (timestamp - self.last_nod_time) > NOD_COOLDOWN:
                    if detect_nod_up(self.data, NOD_MIN_AMPLITUDE):
                        self.setup = False
                        logging.info("Nod up detected, setup complete!")
                    elif self.setup and detect_roll(self.data, ROLL_MIN_AMPLITUDE):
                        logging.info("Roll detected, saving object position.")
                        position = {"yaw": yaw, "pitch": pitch}
                        self.object_pos = pd.concat([self.object_pos, pd.DataFrame([position])], ignore_index=True)
                        print(self.object_pos)
                    self.last_nod_time = timestamp
            except Exception as e:
                logging.error(f"Error decoding data: {e}")

    def save_log(self, path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.data.to_csv(path, index=False)
        logging.info(f"Saved log to {path}")
