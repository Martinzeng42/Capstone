import numpy as np
import pandas as pd
import logging
import os
import struct
from sensortile.movement_detection import detect_nod_up, detect_roll
from utils.constants import CSV_HEADERS, NOD_TIME_WINDOW, NOD_MIN_AMPLITUDE, SAVE_LOGS, NOD_COOLDOWN, ROLL_MIN_AMPLITUDE

class SensorTileHandler:
    def __init__(self, ips, scan):
        self.data = pd.DataFrame(columns=CSV_HEADERS)
        self.object_pos = pd.DataFrame(columns=["ip", "yaw", "pitch"])
        self.last_nod_time = None
        self.setup = True
        self.ips = ips
        self.scan = scan
        self.object_index = 0
        self.state = {ip : False for ip in self.ips}

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
                    if detect_nod_up(self.data, NOD_MIN_AMPLITUDE) and not self.setup:
                        ip = self.find_closest_view(yaw, pitch)['ip']
                        logging.info(f"The closest object position is the {ip}")
                        self.state[ip] = not self.state[ip]
                        self.scan.run_command(ip, self.state[ip])
                        
                    elif self.setup and detect_roll(self.data, ROLL_MIN_AMPLITUDE):
                        logging.info(f"Roll detected, saving {self.connected_objects[self.object_index]}'s position -> Yaw: {yaw:.2f}, Pitch: {pitch:.2f}")
                        position = {"ip": self.connected_objects[self.object_index], "yaw": yaw, "pitch": pitch}
                        self.object_pos = pd.concat([self.object_pos, pd.DataFrame([position])], ignore_index=True)
                        self.object_index += 1
                        if self.object_index == len(self.connected_objects):
                            self.setup = False
                            logging.info("Setup complete")
                    self.last_nod_time = timestamp
            except Exception as e:
                logging.error(f"Error decoding data: {e}")

    def save_log(self, path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.data.to_csv(path, index=False)
        logging.info(f"Saved log to {path}")
    
    def angular_distance(self, yaw1, pitch1, yaw2, pitch2):
        yaw1 = yaw1 % 360
        yaw2 = yaw2 % 360
        
        dyaw = np.abs(yaw1 - yaw2)
        dyaw = np.minimum(dyaw, 360 - dyaw)

        dpitch = np.abs(pitch1 - pitch2)

        return np.sqrt(dyaw**2 + dpitch**2)

    def find_closest_view(self, new_yaw, new_pitch):
        distances = self.object_pos.apply(
            lambda row: self.angular_distance(new_yaw, new_pitch, row['yaw'], row['pitch']),
            axis=1
        )
        closest_idx = distances.idxmin()
        return self.object_pos.loc[closest_idx]
