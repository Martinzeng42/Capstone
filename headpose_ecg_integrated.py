import asyncio
from bleak import BleakClient
import csv
import logging
import os
import pandas as pd
import struct
import numpy as np
import time
from datetime import datetime
from mac import ADDRESS

# UUIDs from your console
SERVICE_UUID = "00000000-0004-11e1-9ab4-0002a5d5c51b"
CHARACTERISTIC_01 = "00000001-0004-11e1-ac36-0002a5d5c51b"  # Notify
CHARACTERISTIC_02 = "00000002-0004-11e1-ac36-0002a5d5c51b"  # Notify + Write

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("logs/sensortile.log"),
        logging.StreamHandler()
    ]
)

# CSV files
HEADPOSE_CSV = "logs/csv/headpose_data.csv"
ECG_CSV = "logs/csv/ecg_data.csv"
RAW_DATA_CSV = "logs/csv/raw_sensor_data.csv"

HEADPOSE_HEADERS = ["timestamp", "yaw", "pitch", "roll"]
ECG_HEADERS = ["timestamp", "ecg_raw", "ecg_filtered", "heart_rate", "r_peak_detected"]
RAW_HEADERS = ["timestamp", "packet_hex", "packet_length", "data_type"]

SAVE_TO_CSV = True

# Initialize CSV files
def init_csv_files():
    for csv_file, headers in [(HEADPOSE_CSV, HEADPOSE_HEADERS), 
                             (ECG_CSV, ECG_HEADERS),
                             (RAW_DATA_CSV, RAW_HEADERS)]:
        if not os.path.isfile(csv_file):
            with open(csv_file, mode='w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(headers)
                logging.info(f"CSV file created: {csv_file}")

init_csv_files()

# Global dataframes
headpose_df = pd.DataFrame(columns=["timestamp", "yaw", "pitch", "roll"])
ecg_df = pd.DataFrame(columns=["timestamp", "ecg_raw", "ecg_filtered", "heart_rate"])

class ECGProcessor:
    def __init__(self, sample_rate=250):
        self.sample_rate = sample_rate
        self.ecg_buffer = []
        self.filtered_buffer = []
        self.r_peaks = []
        self.last_r_peak_time = 0
        self.heart_rate = 0
        
        # Simple high-pass filter coefficients (removes DC offset)
        self.hp_prev_input = 0
        self.hp_prev_output = 0
        self.hp_alpha = 0.99
        
        # Simple low-pass filter coefficients (removes high frequency noise)
        self.lp_prev_output = 0
        self.lp_alpha = 0.1
    
    def high_pass_filter(self, input_val):
        """Simple high-pass filter to remove DC offset"""
        output = self.hp_alpha * (self.hp_prev_output + input_val - self.hp_prev_input)
        self.hp_prev_input = input_val
        self.hp_prev_output = output
        return output
    
    def low_pass_filter(self, input_val):
        """Simple low-pass filter to remove noise"""
        output = self.lp_alpha * input_val + (1 - self.lp_alpha) * self.lp_prev_output
        self.lp_prev_output = output
        return output
    
    def process_ecg_sample(self, raw_value):
        """Process a single ECG sample"""
        # Apply filters
        hp_filtered = self.high_pass_filter(raw_value)
        filtered = self.low_pass_filter(hp_filtered)
        
        # Add to buffers
        self.ecg_buffer.append(raw_value)
        self.filtered_buffer.append(filtered)
        
        # Keep buffer size manageable
        if len(self.ecg_buffer) > 1000:
            self.ecg_buffer = self.ecg_buffer[-500:]
            self.filtered_buffer = self.filtered_buffer[-500:]
        
        # Detect R-peaks (simplified)
        r_peak_detected = self.detect_r_peak(filtered)
        
        return filtered, r_peak_detected
    
    def detect_r_peak(self, current_sample):
        """Simple R-peak detection algorithm"""
        if len(self.filtered_buffer) < 10:
            return False
        
        # Get recent samples for peak detection
        recent_samples = self.filtered_buffer[-10:]
        current_time = time.time()
        
        # Simple peak detection: current sample is higher than neighbors
        # and above a threshold
        if len(recent_samples) >= 5:
            threshold = np.mean(recent_samples) + 2 * np.std(recent_samples)
            
            if (current_sample > threshold and 
                current_sample > recent_samples[-2] and 
                current_sample > recent_samples[-3] and
                (current_time - self.last_r_peak_time) > 0.3):  # Refractory period
                
                self.r_peaks.append(current_time)
                self.last_r_peak_time = current_time
                
                # Calculate heart rate from recent R-peaks
                if len(self.r_peaks) >= 2:
                    recent_peaks = [p for p in self.r_peaks if current_time - p < 10]  # Last 10 seconds
                    if len(recent_peaks) >= 2:
                        intervals = np.diff(recent_peaks)
                        avg_interval = np.mean(intervals)
                        self.heart_rate = 60.0 / avg_interval if avg_interval > 0 else 0
                
                # Clean old peaks
                self.r_peaks = [p for p in self.r_peaks if current_time - p < 30]
                
                return True
        
        return False

# Global ECG processor
ecg_processor = ECGProcessor()

def parse_ecg_from_packet(data):
    """Try to extract ECG data from the BLE packet"""
    ecg_values = []
    
    # Method 1: Look for ECG data in specific byte positions
    # You'll need to determine where ECG data is located in your packet
    
    # Method 2: Try to find patterns that look like ECG data
    # ECG data is typically 12-16 bit values from ADC
    for i in range(0, len(data) - 1, 2):
        try:
            # Try 16-bit signed integer
            value = struct.unpack("<h", data[i:i+2])[0]
            # Check if it's in reasonable ECG range (adjust based on your ADC)
            if -2048 < value < 2048:  # 12-bit signed range
                ecg_values.append(value)
        except:
            continue
    
    # Method 3: Try 32-bit floats if ECG is sent as processed data
    for i in range(0, len(data) - 3, 4):
        try:
            value = struct.unpack("<f", data[i:i+4])[0]
            if -10.0 < value < 10.0:  # Reasonable ECG voltage range in mV
                ecg_values.append(value)
        except:
            continue
    
    return ecg_values

def log_ecg_data(timestamp, ecg_raw, ecg_filtered, heart_rate, r_peak):
    """Log ECG data to CSV and DataFrame"""
    global ecg_df
    
    if SAVE_TO_CSV:
        with open(ECG_CSV, mode='a', newline='') as f:
            writer = csv.writer(f)
            writer.writerow([timestamp.isoformat(), ecg_raw, ecg_filtered, heart_rate, r_peak])
    
    # Add to DataFrame
    new_row = {"timestamp": timestamp, "ecg_raw": ecg_raw, "ecg_filtered": ecg_filtered, "heart_rate": heart_rate}
    ecg_df = pd.concat([ecg_df, pd.DataFrame([new_row])], ignore_index=True)
    
    # Keep last 30 seconds
    ecg_df = ecg_df[ecg_df["timestamp"] > timestamp - pd.Timedelta(seconds=30)]

def log_raw_data(timestamp, data):
    """Log raw packet data"""
    if SAVE_TO_CSV:
        with open(RAW_DATA_CSV, mode='a', newline='') as f:
            writer = csv.writer(f)
            writer.writerow([timestamp.isoformat(), data.hex(), len(data), "ble_packet"])

def notification_handler(sender, data):
    """Enhanced notification handler for both head pose and ECG data"""
    logging.info(f"\nNotification from {sender}:")
    logging.info(f"Hex: {data.hex()}")
    logging.info(f"Length: {len(data)} bytes")
    
    global headpose_df
    timestamp = pd.Timestamp.now()
    
    # Log raw data
    log_raw_data(timestamp, data)
    
    # Parse head pose data (existing logic)
    if len(data) >= 20:
        try:
            # Head Pose starts at byte 9, 3 floats (12 bytes total)
            yaw, pitch, roll = struct.unpack("<fff", data[9:21])
            
            # Log to DataFrame
            new_row = {"timestamp": timestamp, "yaw": yaw, "pitch": pitch, "roll": roll}
            headpose_df = pd.concat([headpose_df, pd.DataFrame([new_row])], ignore_index=True)
            headpose_df = headpose_df[headpose_df["timestamp"] > timestamp - pd.Timedelta(seconds=30)]
            
            logging.info(f"Head Pose -> Yaw: {yaw:.2f}, Pitch: {pitch:.2f}, Roll: {roll:.2f}")
            
            # Save head pose to CSV
            if SAVE_TO_CSV:
                with open(HEADPOSE_CSV, mode='a', newline='') as f:
                    writer = csv.writer(f)
                    writer.writerow([timestamp.isoformat(), yaw, pitch, roll])
                    
        except Exception as e:
            logging.error(f"Error decoding head pose: {e}")
    
    # Try to extract ECG data from the packet
    ecg_values = parse_ecg_from_packet(data)
    
    if ecg_values:
        logging.info(f"Found potential ECG values: {ecg_values[:5]}...")  # Show first 5 values
        
        # Process each ECG sample
        for ecg_raw in ecg_values[:10]:  # Process up to 10 samples per packet
            try:
                ecg_filtered, r_peak_detected = ecg_processor.process_ecg_sample(float(ecg_raw))
                
                if r_peak_detected:
                    logging.info(f"R-peak detected! HR: {ecg_processor.heart_rate:.1f} BPM")
                
                # Log ECG data
                log_ecg_data(timestamp, ecg_raw, ecg_filtered, ecg_processor.heart_rate, r_peak_detected)
                
                # Log significant ECG info
                if len(ecg_processor.ecg_buffer) % 50 == 0:  # Every 50 samples
                    logging.info(f"ECG -> Raw: {ecg_raw:.2f}, Filtered: {ecg_filtered:.4f}, HR: {ecg_processor.heart_rate:.1f} BPM")
                    
            except Exception as e:
                logging.error(f"Error processing ECG sample {ecg_raw}: {e}")

async def send_ecg_commands(client):
    """Send commands to try to enable ECG data streaming"""
    ecg_commands = [
        # Try different command patterns for ECG
        bytearray([0x33, 0x01, 0x0A]),  # Variant of your start command
        bytearray([0x32, 0x02, 0x0A]),  # Different data type
        bytearray([0x32, 0x01, 0x0B]),  # Different parameter
        bytearray([0x34, 0x01, 0x0A]),  # Different command
        bytearray([0x35, 0x01, 0x0A]),  # Another variant
        bytearray([0x32, 0x01, 0x0A, 0x01]),  # Additional ECG flag
    ]
    
    logging.info("Trying to enable ECG data streaming...")
    
    for i, cmd in enumerate(ecg_commands):
        try:
            logging.info(f"Sending ECG command {i+1}: {cmd.hex()}")
            await client.write_gatt_char(CHARACTERISTIC_02, cmd, response=False)
            await asyncio.sleep(0.5)  # Wait for response
            
        except Exception as e:
            logging.error(f"Error sending ECG command {cmd.hex()}: {e}")

async def main():
    logging.info("Connecting to SensorTile...")
    async with BleakClient(ADDRESS, timeout=60) as client:
        if not client.is_connected:
            logging.error("Failed to connect to SensorTile.")
            return
        logging.info("Connected to SensorTile.")

        # Print characteristics
        logging.info("Characteristic properties:")
        for service in client.services:
            for char in service.characteristics:
                logging.info(f"{char.uuid} -> {char.properties}")

        await client.start_notify(CHARACTERISTIC_01, notification_handler)
        await client.start_notify(CHARACTERISTIC_02, notification_handler)
        logging.info("Subscribed to both characteristics.")

        # Send original head pose command
        logging.info("Sending start command (32 01 0A)...")
        await client.write_gatt_char(CHARACTERISTIC_02, bytearray([0x32, 0x01, 0x0A]), response=False)
        logging.info("Sent start command (32 01 0A)")
        
        await asyncio.sleep(1)
        
        # Try ECG commands
        await send_ecg_commands(client)

        logging.info("Begin streaming head pose and ECG data...")

        # Wait and process notifications
        try:
            while True:
                await asyncio.sleep(1)
                
                # Print periodic status
                if len(ecg_processor.ecg_buffer) > 0:
                    logging.info(f"ECG Status - Samples: {len(ecg_processor.ecg_buffer)}, "
                               f"Heart Rate: {ecg_processor.heart_rate:.1f} BPM, "
                               f"R-peaks: {len(ecg_processor.r_peaks)}")
                    
        except KeyboardInterrupt:
            logging.info("Stopping...")

        # Stop notifications on exit
        await client.stop_notify(CHARACTERISTIC_01)
        await client.stop_notify(CHARACTERISTIC_02)

if __name__ == "__main__":
    # Run main loop
    asyncio.run(main())