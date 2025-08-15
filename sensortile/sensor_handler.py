import numpy as np
import pandas as pd
import logging
import time
import os
import struct
import threading
from sensortile.movement_detection import detect_nod, detect_roll, detect_nod_up, detect_nod_down
import sensortile.multiple_blinks_detection as blink
from utils.constants import CSV_HEADERS, NOD_TIME_WINDOW, NOD_MIN_AMPLITUDE, SAVE_LOGS, NOD_COOLDOWN, ROLL_MIN_AMPLITUDE, CALIBRATION_WAIT_S, CALIBRATION_WAIT_TRIGGER, CALIBRATION_COOLDOWN

class SensorTileHandler:
    def __init__(self, devices, scan):
        self.data = pd.DataFrame(columns=CSV_HEADERS)
        self.object_pos = pd.DataFrame(columns=["ip", "name", "yaw", "pitch"])
        self.last_nod_time = None
        self.last_roll_time = None
        self.setup = True
        # self.setup = False    #! testing only normal mode, so skip
        self.devices = devices      # List of IP strings
        # self.devices = {"test1", "test2"}
        self.scan = scan            # Scan_Network class instance
        self.object_index = 0
        self.state = {device : False for device in self.devices}        # Keep on track Devices state


        # Calibration variables       
        self.mode = "NORMAL"          #! testing only normal mode, so skip  
        self.cal_index = 0
        self.cal_started_at = None
        self.cal_current_ip = None

        # Camera object detection Variables
        # self.cam = cam
        # self.cam_state = False
        # self.cam.thread = None
        
        # Thread to thread communication
        self.cmd_queue = None   # will be assigned from main
        self.cam_data_queue = None   # will be assigned from main

        # vAFE offset correction -> make baseline ~0
        self.vafe_offset_correction_mv = 2000    #! put your value, should update this in the calibration mode
    

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
        # Turn off previous device if any
        if self.cal_current_ip is not None:
            try:
                self.scan.run_command(self.cal_current_ip, False)
            except Exception as e:
                logging.warning(f"Failed to turn off {self.cal_current_ip}: {e}")
        
        # Finished condition
        if self.cal_index >= len(self.devices):
            self.mode = "NORMAL"
            self.setup = False
            self.cal_current_ip = None
            logging.info("Setup complete and Calibration finished. Entering NORMAL mode.")
            return

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
                # logging.info("Packet received")
                yaw, pitch, roll = struct.unpack("<fff", data[33:45])
                vafe = struct.unpack("<f", data[61:65])[0]      # equivalent to eog_raw_lsb in Martin's code
                vafe += self.vafe_offset_correction_mv
                timestamp = pd.Timestamp.now()

                new_row = {"timestamp": timestamp, "yaw": yaw, "pitch": pitch, "roll": roll, "vafe": vafe}
                self.data = pd.concat([self.data, pd.DataFrame([new_row])], ignore_index=True)
                self.data = self.data[self.data["timestamp"] > timestamp - pd.Timedelta(seconds=NOD_TIME_WINDOW)]

                # EOG stuff
                eog_raw_mv = vafe / blink.VAFE_GAIN_LSB_PER_MV

                # Add to buffers
                blink.signal_buffer.append(eog_raw_mv)
                blink.baseline_buffer.append(eog_raw_mv)

                if len(blink.signal_buffer) < blink.MIN_LENGTH_FOR_FILTER:
                    return
                
                # Apply enhanced filtering (keep the good noise reduction)
                signal_array = np.array(list(blink.signal_buffer))
                filtered_signal = blink.apply_enhanced_eog_filter(signal_array, blink.FS)
                
                # Calculate robust baseline
                baseline = blink.calculate_robust_baseline(list(blink.baseline_buffer))

                # Use original hard blink detection (every sample for responsiveness)
                blink_detected = blink.detect_hard_blinks_original(filtered_signal, baseline, blink.FS)

                if len(blink.signal_buffer) % 60 == 0:
                    baseline_corrected = abs(filtered_signal[-1] - baseline)
                    
                    logging.debug(
                        f"EOG: raw={eog_raw_mv:.2f}mV | filtered={filtered_signal[-1]:.2f}mV | "
                        f"baseline={baseline:.2f}mV | corrected={baseline_corrected:.2f}mV | "
                        f"thresh={blink.HARD_BLINK_THRESH:.2f}mV"
                    )

                if SAVE_LOGS:
                    logging.info(f"Head Pose -> Yaw: {yaw:.2f}, Pitch: {pitch:.2f}, Roll: {roll:.2f}, Vafe: {vafe:.2f}")
                
                # === SETUP MODE TO START CALIBRATION===
                if self.setup and self.mode != "CALIBRATING":
                    if detect_nod(self.data, NOD_MIN_AMPLITUDE) and ((self.last_nod_time is None) or ((timestamp - self.last_nod_time) > CALIBRATION_COOLDOWN and (timestamp - self.last_nod_time).total_seconds() < CALIBRATION_WAIT_TRIGGER)):
                        # simple counter for nods
                        if not hasattr(self, "setup_nod_count"):
                            self.setup_nod_count = 0
                        self.setup_nod_count += 1
                        self.last_nod_time = timestamp
                        logging.info(f"[SETUP] Nod detected ({self.setup_nod_count}/2)")
                        if self.setup_nod_count >= 2:
                            logging.info("[SETUP] Double nod detected — starting calibration.")
                            self.start_calibration()

                # === CALIBRATION MODE ===
                if self.mode == "CALIBRATING":
                    # Timeout handling
                    if (timestamp - self.cal_started_at).total_seconds() > CALIBRATION_WAIT_S:
                        logging.info(f"[CALIBRATE] Timeout for {self.cal_current_ip}. Skipping...")
                        self.cal_index += 1
                        self.calibration_step()

                    # Check gestures
                    # NOD = confirm mapping
                    if (self.last_nod_time is None) or (timestamp - self.last_nod_time) > NOD_COOLDOWN:
                        if detect_nod(self.data, NOD_MIN_AMPLITUDE):
                            logging.info(f"Please wait and stay still to detect object and confirm object position...")
                            
                            self.last_nod_time = timestamp
                            ip = self.cal_current_ip
                            
                            # turn on camera
                            self.cmd_queue.put("start_stream")
                            
                            # retrieve detected object: I copy pasted the code from line 199
                            if not self.cam_data_queue.empty():
                                detected_object = self.cam_data_queue.get()
                                # save mapping: current viewing pose → this IP
                                self.object_pos = pd.concat([self.object_pos, pd.DataFrame([{"ip": ip, "name": detected_object, "yaw": float(yaw), "pitch": float(pitch)}])], ignore_index=True)
                                logging.info(f"[CALIBRATE] Confirmed {detected_object} at yaw={yaw:.1f}, pitch={pitch:.1f}")
                            
                            else:
                                logging.warning("No object detected by the camera. Skipping...")

                            
                            # turn off and advance
                            try:
                                self.scan.run_command(ip, False)
                            except Exception as e:
                                logging.warning(f"Failed to turn off {ip}: {e}")
                            self.cal_index += 1
                            self.calibration_step()

                    # SHAKE (roll) = reject/skip
                    if (self.last_roll_time is None) or (timestamp - self.last_roll_time) > CALIBRATION_COOLDOWN:
                        if detect_roll(self.data, ROLL_MIN_AMPLITUDE):
                            self.last_roll_time = timestamp
                            logging.info(f"[CALIBRATE] Rejected {self.cal_current_ip} by head shake.")
                            self.cal_index += 1
                            self.calibration_step()

                # === NORMAL MODE ===
                if self.mode == "NORMAL" and not self.setup:
                    # logging.info("Normal mode")

                    # check if cam_data_queue has something
                    if not self.cam_data_queue.empty():
                        detected_object = self.cam_data_queue.get()
                        # detected_object = self.cam_data_queue.get_nowait()   # get() should never wait anyways since we check if queue is empty. Also get_nowait() will raise an error if empty so need to put in try except block
                        if detected_object:
                            print("Data type of detected_object", type(detected_object))
                            temp_test = detected_object  # get the first detected object
                            print("Locked onto object:", temp_test)
                            # listen for gestures for commanding the object
                            # if other unused gesture... send commmand to smart device (bluetooth/wifi/wtv protocol)
                        
                    # detect_nod → toggle closest device
                    if (self.last_nod_time is None or (timestamp - self.last_nod_time) > NOD_COOLDOWN) and detect_nod_down(self.data, NOD_MIN_AMPLITUDE):
                        logging.info("Gesture detected ===========================================")
                        ip, name = self.find_closest_view(yaw, pitch)
                        logging.info(f"The closest object position is the {name}, its ip is {ip}")
                        self.state[ip] = not self.state[ip]
                        try:
                            self.scan.run_command(ip, self.state[ip])
                        except Exception as e:
                            logging.warning(f"Failed to toggle {ip}: {e}")
                        self.last_nod_time = timestamp 

                        # turn on camera
                        self.cmd_queue.put("start_stream")
                        

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
        return self.object_pos.loc[closest_idx]["ip"], self.object_pos.loc[closest_idx]["name"]
