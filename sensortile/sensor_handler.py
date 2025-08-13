import numpy as np
import pandas as pd
import logging
import os
import struct
from sensortile.movement_detection import detect_nod, detect_roll, detect_nod_up, detect_nod_down
from utils.constants import CSV_HEADERS, NOD_TIME_WINDOW, NOD_MIN_AMPLITUDE, SAVE_LOGS, NOD_COOLDOWN, ROLL_MIN_AMPLITUDE, CALIBRATION_WAIT_S, CALIBRATION_COOLDOWN

class SensorTileHandler:
    def __init__(self, devices, scan):
        self.data = pd.DataFrame(columns=CSV_HEADERS)
        self.object_pos = pd.DataFrame(columns=["item", "yaw", "pitch"])
        self.last_nod_time = None
        self.setup = True
        self.devices = devices      # List of IP strings
        self.scan = scan            # Scan_Network class instance
        self.object_index = 0
        self.state = {device : False for device in self.devices}        # Keep on track Devices state


        # Calibration variables
        self.mode = "normal"        
        self.cal_index = 0
        self.cal_started_at = None
        self.cal_current_ip = None
    

    def start_calibration(self):
        if not self.devices:
            logging.warning("No devices found to calibrate.")
            return
        self.mode = "CALIBRATING"
        self.cal_index = 0
        self.cal_current_ip = None
        self.cal_started_at = None
        logging.info("Calibration mode started.")
        self.calibration_step()

    def calibration_step(self):
        # Finished condition
        if self.cal_index >= len(self.devices):
            self.mode = "NORMAL"
            self.setup = False
            self.cal_current_ip = None
            logging.info("Setup complete and Calibration finished. Entering NORMAL mode.")
            return

        # Turn off previous device if any
        if self.cal_current_ip is not None:
            try:
                self.scan.run_command(self.cal_current_ip, False)
            except Exception as e:
                logging.warning(f"Failed to turn off {self.cal_current_ip}: {e}")
        
        # Move to next device
        self.cal_current_ip = self.devices[self.cal_index]
        self.cal_started_at = pd.Timestamp.now()
        logging.info(f"[CALIBRATE] Testing {self.cal_current_ip}: turning ON and waiting for nod=confirm / shake=skip.")

        try:
            # Turn on the smart device
            self.scan.run_command(self.cal_current_ip, True)
        except Exception as e:
            logging.warning(f"Failed to turn on {self.cal_current_ip}: {e}")

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
                
                # === CALIBRATION MODE ===
                if self.mode == "CALIBRATING":
                    # Timeout handling
                    if self.cal_started_at and (timestamp - self.cal_started_at).total_seconds() > CALIBRATION_WAIT_S:
                        logging.info(f"[CALIBRATE] Timeout for {self.cal_current_ip}. Skipping...")
                        self.cal_index += 1
                        self.calibration_step()
                        return

                    # Check gestures
                    # NOD = confirm mapping
                    if (self.last_nod_time is None) or (timestamp - self.last_nod_time) > NOD_COOLDOWN:
                        if detect_nod_down(self.data, NOD_MIN_AMPLITUDE):
                            self.last_nod_time = timestamp
                            ip = self.cal_current_ip
                            # save mapping: current viewing pose → this IP
                            self.object_pos = pd.concat([self.object_pos, pd.DataFrame([{"item": ip, "yaw": float(yaw), "pitch": float(pitch)}])], ignore_index=True)
                            logging.info(f"[CALIBRATE] Confirmed {ip} at yaw={yaw:.1f}, pitch={pitch:.1f}")
                            # turn off and advance
                            try:
                                self.scan.run_command(ip, False)
                            except Exception as e:
                                logging.warning(f"Failed to turn off {ip}: {e}")
                            self.cal_index += 1
                            self.calibration_step()
                            return

                    # SHAKE (roll) = reject/skip
                    if (self.last_roll_time is None) or (timestamp - self.last_roll_time) > CALIBRATION_COOLDOWN:
                        if detect_roll(self.data, ROLL_MIN_AMPLITUDE):
                            self.last_roll_time = timestamp
                            logging.info(f"[CALIBRATE] Rejected {self.cal_current_ip} by head shake.")
                            self.cal_index += 1
                            self.calibration_step()
                            return

                    # Stay in CALIBRATING until one of the above happens
                    return

                # === NORMAL MODE ===
                # detect_nod → toggle closest device
                if self.last_nod_time is None or (timestamp - self.last_nod_time) > NOD_COOLDOWN:
                    if not self.setup and detect_nod(self.data, NOD_MIN_AMPLITUDE):
                        ip = self.find_closest_view(yaw, pitch)['item']
                        logging.info(f"The closest object position is the {ip}")
                        self.state[ip] = not self.state[ip]
                        try:
                            self.scan.run_command(ip, self.state[ip])
                        except Exception as e:
                            logging.warning(f"Failed to toggle {ip}: {e}")
                        
                    # elif self.setup and detect_roll(self.data, ROLL_MIN_AMPLITUDE):
                    #     logging.info(f"Roll detected, saving {self.devices[self.object_index]}'s position -> Yaw: {yaw:.2f}, Pitch: {pitch:.2f}")
                    #     position = {"item": self.devices[self.object_index], "yaw": yaw, "pitch": pitch}
                    #     self.object_pos = pd.concat([self.object_pos, pd.DataFrame([position])], ignore_index=True)
                    #     self.object_index += 1
                    #     if self.object_index == len(self.devices):
                    #         self.setup = False
                    #         logging.info("Setup complete")
                    # self.last_nod_time = timestamp
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
