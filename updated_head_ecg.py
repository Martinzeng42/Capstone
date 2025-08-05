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
COMBINED_CSV = "logs/csv/combined_sensor_data.csv"

HEADPOSE_HEADERS = ["timestamp", "yaw", "pitch", "roll"]
ECG_HEADERS = ["timestamp", "ecg_raw", "ecg_filtered", "heart_rate", "r_peak_detected"]
RAW_HEADERS = ["timestamp", "packet_hex", "packet_length", "data_type", "characteristic"]
COMBINED_HEADERS = ["timestamp", "yaw", "pitch", "roll", "ecg_raw", "ecg_filtered", "heart_rate", "packet_counter"]

SAVE_TO_CSV = True

# Packet structure based on AlgoBuilder format
class SensorDataPacket:
    """Structure matching the AlgoBuilder sensor_data_packet_t"""
    def __init__(self):
        self.timestamp = 0
        self.head_pose = [0.0, 0.0, 0.0]  # Yaw, Pitch, Roll
        self.ecg_value = 0.0
        self.packet_counter = 0
    
    @classmethod
    def from_bytes(cls, data):
        """Parse sensor data packet from bytes"""
        packet = cls()
        try:
            if len(data) >= 20:  # Minimum expected packet size
                # Try different parsing approaches based on your AlgoBuilder structure
                
                # Approach 1: Standard AlgoBuilder packet (timestamp + head_pose + ecg + counter)
                if len(data) >= 20:
                    packet.timestamp = struct.unpack("<I", data[0:4])[0]
                    packet.head_pose[0] = struct.unpack("<f", data[4:8])[0]   # Yaw
                    packet.head_pose[1] = struct.unpack("<f", data[8:12])[0]  # Pitch  
                    packet.head_pose[2] = struct.unpack("<f", data[12:16])[0] # Roll
                    packet.ecg_value = struct.unpack("<f", data[16:20])[0]
                    if len(data) >= 22:
                        packet.packet_counter = struct.unpack("<H", data[20:22])[0]
                    return packet
                
        except struct.error as e:
            logging.debug(f"Packet parsing error: {e}")
            return None
        
        return None

# Initialize CSV files
def init_csv_files():
    for csv_file, headers in [(HEADPOSE_CSV, HEADPOSE_HEADERS), 
                             (ECG_CSV, ECG_HEADERS),
                             (RAW_DATA_CSV, RAW_HEADERS),
                             (COMBINED_CSV, COMBINED_HEADERS)]:
        if not os.path.isfile(csv_file):
            os.makedirs(os.path.dirname(csv_file), exist_ok=True)
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
        self.rr_intervals = []
        
        # Improved filter coefficients
        # High-pass filter (0.5 Hz cutoff) - removes DC offset and baseline wander
        self.hp_prev_input = 0
        self.hp_prev_output = 0
        self.hp_alpha = 0.996  # More aggressive high-pass
        
        # Low-pass filter (40 Hz cutoff) - removes high frequency noise
        self.lp_prev_output = 0
        self.lp_alpha = 0.2
        
        # Notch filter for 50/60Hz power line interference
        self.notch_buffer = [0, 0, 0]
        
        # Peak detection parameters
        self.peak_threshold_factor = 1.5
        self.refractory_period = 0.25  # 250ms minimum between R-peaks
        self.adaptive_threshold = 0.0
        self.noise_level = 0.0
        
    def notch_filter_50hz(self, input_val):
        """Simple notch filter for 50Hz power line interference"""
        # Simple moving average notch filter
        self.notch_buffer[0] = self.notch_buffer[1]
        self.notch_buffer[1] = self.notch_buffer[2]
        self.notch_buffer[2] = input_val
        
        # Return filtered value (removes 50Hz component)
        return (self.notch_buffer[0] + self.notch_buffer[2] - 2 * self.notch_buffer[1]) * 0.5 + self.notch_buffer[1]
    
    def high_pass_filter(self, input_val):
        """Improved high-pass filter to remove DC offset and baseline wander"""
        output = self.hp_alpha * (self.hp_prev_output + input_val - self.hp_prev_input)
        self.hp_prev_input = input_val
        self.hp_prev_output = output
        return output
    
    def low_pass_filter(self, input_val):
        """Low-pass filter to remove high frequency noise"""
        output = self.lp_alpha * input_val + (1 - self.lp_alpha) * self.lp_prev_output
        self.lp_prev_output = output
        return output
    
    def process_ecg_sample(self, raw_value):
        """Process a single ECG sample with improved filtering"""
        # Convert to reasonable ECG range (assuming 12-bit ADC, 3.3V reference)
        if isinstance(raw_value, int) and raw_value > 1000:
            # Convert ADC counts to mV (assuming 12-bit ADC)
            ecg_mv = (raw_value / 4096.0 * 3.3 - 1.65) * 1000  # Center around 0, convert to mV
        else:
            ecg_mv = float(raw_value)
        
        # Apply cascaded filters
        notch_filtered = self.notch_filter_50hz(ecg_mv)
        hp_filtered = self.high_pass_filter(notch_filtered)
        lp_filtered = self.low_pass_filter(hp_filtered)
        
        # Add to buffers
        self.ecg_buffer.append(ecg_mv)
        self.filtered_buffer.append(lp_filtered)
        
        # Keep buffer size manageable (about 4 seconds of data)
        buffer_size = self.sample_rate * 4
        if len(self.ecg_buffer) > buffer_size:
            self.ecg_buffer = self.ecg_buffer[-buffer_size//2:]
            self.filtered_buffer = self.filtered_buffer[-buffer_size//2:]
        
        # Update adaptive threshold and noise level
        self.update_adaptive_parameters()
        
        # Detect R-peaks with improved algorithm
        r_peak_detected = self.detect_r_peak_advanced(lp_filtered)
        
        return lp_filtered, r_peak_detected
    
    def update_adaptive_parameters(self):
        """Update adaptive threshold and noise estimation"""
        if len(self.filtered_buffer) >= 50:
            recent_samples = np.array(self.filtered_buffer[-50:])
            self.noise_level = np.std(recent_samples)
            self.adaptive_threshold = np.mean(recent_samples) + self.peak_threshold_factor * self.noise_level
    
    def detect_r_peak_advanced(self, current_sample):
        """Advanced R-peak detection with adaptive thresholding"""
        if len(self.filtered_buffer) < 10:
            return False
        
        current_time = time.time()
        
        # Check refractory period
        if (current_time - self.last_r_peak_time) < self.refractory_period:
            return False
        
        # Get recent samples for analysis
        if len(self.filtered_buffer) >= 5:
            recent_samples = np.array(self.filtered_buffer[-5:])
            
            # Peak detection criteria:
            # 1. Current sample is above adaptive threshold
            # 2. Current sample is local maximum
            # 3. Sufficient time has passed since last R-peak
            
            is_above_threshold = current_sample > self.adaptive_threshold
            is_local_max = (current_sample > recent_samples[-2] and 
                           current_sample > recent_samples[-3])
            
            if is_above_threshold and is_local_max:
                self.r_peaks.append(current_time)
                self.last_r_peak_time = current_time
                
                # Calculate heart rate from R-R intervals
                self.calculate_heart_rate()
                
                # Clean old peaks (keep last 30 seconds)
                self.r_peaks = [p for p in self.r_peaks if current_time - p < 30]
                
                logging.info(f"🫀 R-peak detected! HR: {self.heart_rate:.1f} BPM")
                return True
        
        return False
    
    def calculate_heart_rate(self):
        """Calculate heart rate from R-R intervals with outlier rejection"""
        if len(self.r_peaks) >= 2:
            # Calculate R-R intervals
            rr_intervals = np.diff(self.r_peaks[-8:])  # Use last 8 peaks for stability
            
            # Remove outliers (intervals outside 0.4-2.0 seconds = 30-150 BPM)
            valid_intervals = rr_intervals[(rr_intervals > 0.4) & (rr_intervals < 2.0)]
            
            if len(valid_intervals) >= 2:
                # Use median for robustness
                median_interval = np.median(valid_intervals)
                self.heart_rate = 60.0 / median_interval
                
                # Store for HRV analysis if needed
                self.rr_intervals = valid_intervals.tolist()
            else:
                # Fallback to simple average
                if len(rr_intervals) > 0:
                    avg_interval = np.mean(rr_intervals)
                    if 0.4 < avg_interval < 2.0:
                        self.heart_rate = 60.0 / avg_interval

# Global ECG processor
ecg_processor = ECGProcessor()

def parse_ecg_from_legacy_packet(data):
    """Legacy parsing for non-structured packets"""
    ecg_values = []
    
    # Method 1: Look for 16-bit values that could be ECG ADC readings
    for i in range(0, len(data) - 1, 2):
        try:
            # Try unsigned 16-bit (typical for ADC)
            value = struct.unpack("<H", data[i:i+2])[0]
            # Check if it's in reasonable ADC range (12-bit ADC = 0-4095)
            if 100 < value < 4000:  # Avoid extreme values
                ecg_values.append(value)
        except struct.error:
            continue
    
    # Method 2: Look for float values
    for i in range(0, len(data) - 3, 4):
        try:
            value = struct.unpack("<f", data[i:i+4])[0]
            # Check if it's in reasonable ECG voltage range
            if -5.0 < value < 5.0 and not np.isnan(value):
                ecg_values.append(value)
        except struct.error:
            continue
    
    return ecg_values

def log_combined_data(timestamp, yaw, pitch, roll, ecg_raw, ecg_filtered, heart_rate, packet_counter):
    """Log combined sensor data"""
    if SAVE_TO_CSV:
        with open(COMBINED_CSV, mode='a', newline='') as f:
            writer = csv.writer(f)
            writer.writerow([timestamp.isoformat(), yaw, pitch, roll, ecg_raw, ecg_filtered, heart_rate, packet_counter])

def log_raw_data(timestamp, data, characteristic):
    """Log raw packet data with characteristic info"""
    if SAVE_TO_CSV:
        with open(RAW_DATA_CSV, mode='a', newline='') as f:
            writer = csv.writer(f)
            writer.writerow([timestamp.isoformat(), data.hex(), len(data), "ble_packet", characteristic])

def notification_handler(sender, data):
    """Enhanced notification handler with structured packet parsing"""
    characteristic = str(sender).split()[-1]  # Extract characteristic UUID
    logging.info(f"\n📡 Notification from {characteristic}:")
    logging.info(f"Hex: {data.hex()}")
    logging.info(f"Length: {len(data)} bytes")
    
    global headpose_df, ecg_df
    timestamp = pd.Timestamp.now()
    
    # Log raw data
    log_raw_data(timestamp, data, characteristic)
    
    # Try to parse as structured packet first
    sensor_packet = SensorDataPacket.from_bytes(data)
    
    if sensor_packet:
        # Successfully parsed structured packet
        logging.info(f"📦 Structured packet #{sensor_packet.packet_counter}")
        logging.info(f"🧭 Head Pose -> Yaw: {sensor_packet.head_pose[0]:.2f}, "
                    f"Pitch: {sensor_packet.head_pose[1]:.2f}, "
                    f"Roll: {sensor_packet.head_pose[2]:.2f}")
        logging.info(f"💓 ECG Raw: {sensor_packet.ecg_value:.3f}")
        
        # Process ECG data
        ecg_filtered, r_peak_detected = ecg_processor.process_ecg_sample(sensor_packet.ecg_value)
        
        # Log to DataFrames
        new_headpose_row = {
            "timestamp": timestamp, 
            "yaw": sensor_packet.head_pose[0], 
            "pitch": sensor_packet.head_pose[1], 
            "roll": sensor_packet.head_pose[2]
        }
        headpose_df = pd.concat([headpose_df, pd.DataFrame([new_headpose_row])], ignore_index=True)
        
        new_ecg_row = {
            "timestamp": timestamp, 
            "ecg_raw": sensor_packet.ecg_value, 
            "ecg_filtered": ecg_filtered, 
            "heart_rate": ecg_processor.heart_rate
        }
        ecg_df = pd.concat([ecg_df, pd.DataFrame([new_ecg_row])], ignore_index=True)
        
        # Save to individual CSV files
        if SAVE_TO_CSV:
            with open(HEADPOSE_CSV, mode='a', newline='') as f:
                writer = csv.writer(f)
                writer.writerow([timestamp.isoformat(), sensor_packet.head_pose[0], 
                               sensor_packet.head_pose[1], sensor_packet.head_pose[2]])
            
            with open(ECG_CSV, mode='a', newline='') as f:
                writer = csv.writer(f)
                writer.writerow([timestamp.isoformat(), sensor_packet.ecg_value, 
                               ecg_filtered, ecg_processor.heart_rate, r_peak_detected])
        
        # Save combined data
        log_combined_data(timestamp, sensor_packet.head_pose[0], sensor_packet.head_pose[1], 
                         sensor_packet.head_pose[2], sensor_packet.ecg_value, ecg_filtered, 
                         ecg_processor.heart_rate, sensor_packet.packet_counter)
    
    else:
        # Fall back to legacy parsing
        logging.info("📦 Using legacy packet parsing")
        
        # Parse head pose data (your existing logic)
        head_pose_parsed = False
        if len(data) >= 20:
            try:
                # Try different byte offsets for head pose data
                for offset in [9, 4, 0]:  # Try different starting positions
                    if offset + 12 <= len(data):
                        yaw, pitch, roll = struct.unpack("<fff", data[offset:offset+12])
                        
                        # Validate head pose values (reasonable angles)
                        if (-180 <= yaw <= 180 and -90 <= pitch <= 90 and -180 <= roll <= 180):
                            logging.info(f"🧭 Head Pose -> Yaw: {yaw:.2f}, Pitch: {pitch:.2f}, Roll: {roll:.2f}")
                            
                            # Log head pose data
                            new_row = {"timestamp": timestamp, "yaw": yaw, "pitch": pitch, "roll": roll}
                            headpose_df = pd.concat([headpose_df, pd.DataFrame([new_row])], ignore_index=True)
                            
                            if SAVE_TO_CSV:
                                with open(HEADPOSE_CSV, mode='a', newline='') as f:
                                    writer = csv.writer(f)
                                    writer.writerow([timestamp.isoformat(), yaw, pitch, roll])
                            
                            head_pose_parsed = True
                            break
                            
            except Exception as e:
                logging.debug(f"Head pose parsing error: {e}")
        
        # Try to extract ECG data using legacy methods
        ecg_values = parse_ecg_from_legacy_packet(data)
        
        if ecg_values:
            logging.info(f"💓 Found {len(ecg_values)} potential ECG values: {ecg_values[:3]}...")
            
            # Process the most recent ECG sample
            if ecg_values:
                ecg_raw = ecg_values[-1]  # Take the last value
                ecg_filtered, r_peak_detected = ecg_processor.process_ecg_sample(ecg_raw)
                
                # Log ECG data
                if SAVE_TO_CSV:
                    with open(ECG_CSV, mode='a', newline='') as f:
                        writer = csv.writer(f)
                        writer.writerow([timestamp.isoformat(), ecg_raw, ecg_filtered, 
                                       ecg_processor.heart_rate, r_peak_detected])
                
                new_ecg_row = {
                    "timestamp": timestamp, 
                    "ecg_raw": ecg_raw, 
                    "ecg_filtered": ecg_filtered, 
                    "heart_rate": ecg_processor.heart_rate
                }
                ecg_df = pd.concat([ecg_df, pd.DataFrame([new_ecg_row])], ignore_index=True)
    
    # Keep DataFrames manageable (last 60 seconds)
    cutoff_time = timestamp - pd.Timedelta(seconds=60)
    headpose_df = headpose_df[headpose_df["timestamp"] > cutoff_time]
    ecg_df = ecg_df[ecg_df["timestamp"] > cutoff_time]

async def send_enhanced_ecg_commands(client):
    """Send enhanced commands to enable ECG data streaming"""
    
    # Commands based on AlgoBuilder structure
    ecg_commands = [
        # Original command
        bytearray([0x32, 0x01, 0x0A]),
        
        # Try to enable ECG specifically
        bytearray([0x33, 0x01, 0x0A]),  # Alternative command
        bytearray([0x32, 0x02, 0x0A]),  # Different data type
        bytearray([0x32, 0x01, 0x0B]),  # Different sensor
        
        # Try commands for combined sensor data
        bytearray([0x32, 0x03, 0x0A]),  # Combined mode
        bytearray([0x34, 0x01, 0x0A]),  # Extended command
        
        # Try enabling specific ECG features
        bytearray([0x32, 0x01, 0x0A, 0x01]),  # With ECG flag
        bytearray([0x32, 0x01, 0x0A, 0xFF]),  # Enable all features
    ]
    
    logging.info("🔧 Trying to enable enhanced ECG data streaming...")
    
    for i, cmd in enumerate(ecg_commands):
        try:
            logging.info(f"📤 Sending command {i+1}/{len(ecg_commands)}: {cmd.hex()}")
            await client.write_gatt_char(CHARACTERISTIC_02, cmd, response=False)
            await asyncio.sleep(0.5)  # Wait for response
            
        except Exception as e:
            logging.error(f"❌ Error sending command {cmd.hex()}: {e}")

async def main():
    logging.info("🔗 Connecting to SensorTile...")
    async with BleakClient(ADDRESS, timeout=60) as client:
        if not client.is_connected:
            logging.error("❌ Failed to connect to SensorTile.")
            return
        logging.info("✅ Connected to SensorTile.")

        # Print characteristics
        logging.info("📋 Characteristic properties:")
        for service in client.services:
            for char in service.characteristics:
                logging.info(f"  {char.uuid} -> {char.properties}")

        # Subscribe to notifications
        await client.start_notify(CHARACTERISTIC_01, notification_handler)
        await client.start_notify(CHARACTERISTIC_02, notification_handler)
        logging.info("🔔 Subscribed to both characteristics.")

        # Send enhanced commands
        await send_enhanced_ecg_commands(client)

        logging.info("📊 Begin streaming enhanced sensor data...")
        logging.info("💡 Press Ctrl+C to stop streaming")

        # Main streaming loop
        try:
            start_time = time.time()
            while True:
                await asyncio.sleep(2)
                
                # Print periodic status
                current_time = time.time()
                elapsed = current_time - start_time
                
                status_lines = [
                    f"⏱️  Runtime: {elapsed:.1f}s",
                    f"📦 ECG samples: {len(ecg_processor.ecg_buffer)}",
                    f"💓 Heart Rate: {ecg_processor.heart_rate:.1f} BPM",
                    f"🫀 R-peaks detected: {len(ecg_processor.r_peaks)}",
                    f"📊 Head pose samples: {len(headpose_df)}",
                    f"🔧 Noise level: {ecg_processor.noise_level:.4f}"
                ]
                
                logging.info("📈 " + " | ".join(status_lines))
                    
        except KeyboardInterrupt:
            logging.info("🛑 Stopping data collection...")

        # Stop notifications
        await client.stop_notify(CHARACTERISTIC_01)
        await client.stop_notify(CHARACTERISTIC_02)
        
        # Final statistics
        logging.info("📊 Final Statistics:")
        logging.info(f"  Total ECG samples: {len(ecg_processor.ecg_buffer)}")
        logging.info(f"  Total R-peaks: {len(ecg_processor.r_peaks)}")
        logging.info(f"  Final heart rate: {ecg_processor.heart_rate:.1f} BPM")
        logging.info(f"  Head pose samples: {len(headpose_df)}")

if __name__ == "__main__":
    # Run main loop
    asyncio.run(main())