# import serial
# import time
# import struct
# import csv
# import logging
# from datetime import datetime
# import pandas as pd

# # Configure logging
# logging.basicConfig(
#     level=logging.INFO,
#     format="%(asctime)s [%(levelname)s] %(message)s",
#     handlers=[
#         logging.FileHandler("logs/serial_monitor.log"),
#         logging.StreamHandler()
#     ]
# )

# # Serial configuration - adjust these based on your setup
# SERIAL_PORT = "COM7"  # Change to your actual port (COM3, COM4, etc. on Windows, /dev/ttyUSB0, /dev/ttyACM0 on Linux)
# BAUD_RATE = 115200    # Common baud rates: 9600, 115200, 230400
# TIMEOUT = 1.0

# # CSV file for logging
# CSV_FILE = "logs/csv/analog_frontend_data.csv"
# CSV_HEADERS = ["timestamp", "raw_data_hex", "parsed_value", "data_type"]

# class SerialDataMonitor:
#     def __init__(self, port, baud_rate, timeout=1.0):
#         self.port = port
#         self.baud_rate = baud_rate
#         self.timeout = timeout
#         self.serial_conn = None
#         self.data_buffer = []
        
#         # Create CSV file
#         with open(CSV_FILE, mode='w', newline='') as f:
#             writer = csv.writer(f)
#             writer.writerow(CSV_HEADERS)
        
#     def connect(self):
#         """Connect to serial port"""
#         try:
#             self.serial_conn = serial.Serial(
#                 self.port, 
#                 self.baud_rate, 
#                 timeout=self.timeout
#             )
#             logging.info(f"Connected to {self.port} at {self.baud_rate} baud")
#             return True
#         except Exception as e:
#             logging.error(f"Failed to connect to {self.port}: {e}")
#             return False
    
#     def disconnect(self):
#         """Disconnect from serial port"""
#         if self.serial_conn and self.serial_conn.is_open:
#             self.serial_conn.close()
#             logging.info("Serial connection closed")
    
#     def parse_data_packet(self, data):
#         """Parse different data packet formats"""
#         timestamp = datetime.now()
#         data_hex = data.hex()
#         parsed_values = []
        
#         # Method 1: Try to parse as ASCII text (if MEMS Studio sends readable text)
#         try:
#             ascii_data = data.decode('ascii').strip()
#             if ascii_data:
#                 logging.info(f"ASCII Data: {ascii_data}")
#                 parsed_values.append(("ascii", ascii_data))
#         except:
#             pass
        
#         # Method 2: Try to parse as binary data with different formats
#         if len(data) >= 4:
#             try:
#                 # Try as float (4 bytes)
#                 float_val = struct.unpack('<f', data[:4])[0]
#                 if -1000000 < float_val < 1000000:  # Reasonable range check
#                     logging.info(f"Float value: {float_val:.6f}")
#                     parsed_values.append(("float", float_val))
#             except:
#                 pass
            
#             try:
#                 # Try as 32-bit integer
#                 int_val = struct.unpack('<i', data[:4])[0]
#                 logging.info(f"Int32 value: {int_val}")
#                 parsed_values.append(("int32", int_val))
#             except:
#                 pass
        
#         if len(data) >= 2:
#             try:
#                 # Try as 16-bit integer (common for ADC values)
#                 int16_val = struct.unpack('<h', data[:2])[0]
#                 logging.info(f"Int16 value: {int16_val}")
#                 parsed_values.append(("int16", int16_val))
#             except:
#                 pass
        
#         # Log to CSV
#         for data_type, value in parsed_values:
#             with open(CSV_FILE, mode='a', newline='') as f:
#                 writer = csv.writer(f)
#                 writer.writerow([timestamp.isoformat(), data_hex, value, data_type])
        
#         return parsed_values
    
#     def look_for_patterns(self, data):
#         """Look for common data patterns"""
#         # Check for common header patterns
#         common_headers = [
#             b'\xAA\x55',  # Common sync pattern
#             b'\xFF\xFE',  # Another common pattern
#             b'DATA',      # ASCII header
#             b'ECG',       # ECG identifier
#         ]
        
#         for header in common_headers:
#             if data.startswith(header):
#                 logging.info(f"Found header pattern: {header.hex()}")
#                 return True
        
#         return False
    
#     def send_commands(self):
#         """Send common commands to request data"""
#         commands = [
#             b'START\r\n',
#             b'GET_DATA\r\n',
#             b'ECG_ON\r\n',
#             b'\x01',  # Simple binary command
#             b'\x02',
#             b'\xAA\x55\x01',  # Command with header
#         ]
        
#         for cmd in commands:
#             try:
#                 logging.info(f"Sending command: {cmd}")
#                 self.serial_conn.write(cmd)
#                 time.sleep(0.1)
                
#                 # Check for response
#                 if self.serial_conn.in_waiting > 0:
#                     response = self.serial_conn.read(self.serial_conn.in_waiting)
#                     logging.info(f"Response to {cmd.hex()}: {response.hex()}")
#                     self.parse_data_packet(response)
#             except Exception as e:
#                 logging.error(f"Error sending command {cmd}: {e}")
    
#     def monitor_continuous(self):
#         """Monitor serial port continuously"""
#         if not self.serial_conn:
#             logging.error("Not connected to serial port")
#             return
        
#         logging.info("Starting continuous monitoring...")
#         logging.info("Press Ctrl+C to stop")
        
#         # Try sending some commands first
#         self.send_commands()
        
#         try:
#             while True:
#                 if self.serial_conn.in_waiting > 0:
#                     # Read available data
#                     data = self.serial_conn.read(self.serial_conn.in_waiting)
                    
#                     if data:
#                         logging.info(f"Received {len(data)} bytes: {data.hex()}")
                        
#                         # Look for patterns
#                         self.look_for_patterns(data)
                        
#                         # Parse the data
#                         self.parse_data_packet(data)
                        
#                         # Add to buffer for pattern analysis
#                         self.data_buffer.extend(data)
                        
#                         # Keep buffer size manageable
#                         if len(self.data_buffer) > 1000:
#                             self.data_buffer = self.data_buffer[-500:]
                
#                 time.sleep(0.01)  # Small delay to prevent CPU overload
                
#         except KeyboardInterrupt:
#             logging.info("Monitoring stopped by user")
    
#     def analyze_buffer_patterns(self):
#         """Analyze collected data for patterns"""
#         if len(self.data_buffer) < 10:
#             return
        
#         logging.info("Analyzing data patterns...")
        
#         # Look for repeating byte sequences
#         for length in [2, 4, 8, 12, 16, 20]:
#             if len(self.data_buffer) >= length * 2:
#                 chunk1 = self.data_buffer[:length]
#                 chunk2 = self.data_buffer[length:length*2]
                
#                 if chunk1 == chunk2:
#                     logging.info(f"Found repeating pattern of {length} bytes: {bytes(chunk1).hex()}")

# def main():
#     # Create monitor instance
#     monitor = SerialDataMonitor(SERIAL_PORT, BAUD_RATE, TIMEOUT)
    
#     # Connect to serial port
#     if monitor.connect():
#         try:
#             # Start monitoring
#             monitor.monitor_continuous()
#         finally:
#             monitor.disconnect()
#             monitor.analyze_buffer_patterns()
#     else:
#         logging.error("Failed to connect to serial port")
#         logging.info("Available ports:")
#         # import serial.tools.list_ports
#         # ports = serial.tools.list_ports.comports()
#         # for port in ports:
#         #     logging.info(f"  {port.device}: {port.description}")

# if __name__ == "__main__":
#     main()


import serial
import time
import struct
import csv
import logging
import math
import re
from datetime import datetime
import pandas as pd
import numpy as np

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("logs/serial_monitor.log"),
        logging.StreamHandler()
    ]
)

# Serial configuration - adjust these based on your setup
SERIAL_PORT = "COM7"  # Change to your actual port
BAUD_RATE = 115200
TIMEOUT = 1.0

# CSV files for logging
HEADPOSE_CSV = "logs/csv/headpose_data.csv"
ECG_CSV = "logs/csv/ecg_data.csv"
RAW_SENSOR_CSV = "logs/csv/raw_sensor_data.csv"

# CSV headers
HEADPOSE_HEADERS = ["timestamp", "yaw", "pitch", "roll", "quat_w", "quat_x", "quat_y", "quat_z"]
ECG_HEADERS = ["timestamp", "ecg_value", "heart_rate", "data_source"]
RAW_SENSOR_HEADERS = ["timestamp", "acc_x", "acc_y", "acc_z", "gyr_x", "gyr_y", "gyr_z", 
                     "mag_x", "mag_y", "mag_z", "temp", "pressure", "qvar"]

class HeadPoseECGMonitor:
    def __init__(self, port, baud_rate, timeout=1.0):
        self.port = port
        self.baud_rate = baud_rate
        self.timeout = timeout
        self.serial_conn = None
        
        # Data storage
        self.sensor_data = {}
        self.ecg_buffer = []
        self.last_sensor_reading = {}
        
        # Calibration offsets (you may need to calibrate these)
        self.gyro_offset = {"x": 0, "y": 0, "z": 0}
        self.accel_calibration = {"scale": 1000.0}  # Assuming LSB/g
        
        # Create CSV files
        self.init_csv_files()
        
    def init_csv_files(self):
        """Initialize CSV files with headers"""
        try:
            # Head pose CSV
            with open(HEADPOSE_CSV, mode='w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(HEADPOSE_HEADERS)
            
            # ECG CSV
            with open(ECG_CSV, mode='w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(ECG_HEADERS)
                
            # Raw sensor CSV
            with open(RAW_SENSOR_CSV, mode='w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(RAW_SENSOR_HEADERS)
                
            logging.info("CSV files initialized")
        except Exception as e:
            logging.error(f"Failed to initialize CSV files: {e}")
    
    def connect(self):
        """Connect to serial port"""
        try:
            self.serial_conn = serial.Serial(
                self.port, 
                self.baud_rate, 
                timeout=self.timeout
            )
            logging.info(f"Connected to {self.port} at {self.baud_rate} baud")
            return True
        except Exception as e:
            logging.error(f"Failed to connect to {self.port}: {e}")
            return False
    
    def disconnect(self):
        """Disconnect from serial port"""
        if self.serial_conn and self.serial_conn.is_open:
            self.serial_conn.close()
            logging.info("Serial connection closed")
    
    def parse_sensor_data(self, ascii_data):
        """Parse sensor data from ASCII format"""
        timestamp = datetime.now()
        sensor_values = {}
        
        # Parse each line of sensor data
        lines = ascii_data.strip().split('\n')
        for line in lines:
            line = line.strip()
            
            # Temperature
            if line.startswith('TEMP:'):
                temp_match = re.search(r'TEMP:\s*([+-]?\d*\.?\d+)', line)
                if temp_match:
                    sensor_values['temp'] = float(temp_match.group(1))
            
            # Pressure
            elif line.startswith('PRESS:'):
                press_match = re.search(r'PRESS:\s*([+-]?\d*\.?\d+)', line)
                if press_match:
                    sensor_values['pressure'] = float(press_match.group(1))
            
            # Accelerometer
            elif 'ACC_X:' in line:
                acc_match = re.search(r'ACC_X:\s*([+-]?\d+),\s*ACC_Y:\s*([+-]?\d+),\s*ACC_Z:\s*([+-]?\d+)', line)
                if acc_match:
                    sensor_values['acc_x'] = int(acc_match.group(1))
                    sensor_values['acc_y'] = int(acc_match.group(2))
                    sensor_values['acc_z'] = int(acc_match.group(3))
            
            # Gyroscope
            elif 'GYR_X:' in line:
                gyr_match = re.search(r'GYR_X:\s*([+-]?\d+),\s*GYR_Y:\s*([+-]?\d+),\s*GYR_Z:\s*([+-]?\d+)', line)
                if gyr_match:
                    sensor_values['gyr_x'] = int(gyr_match.group(1))
                    sensor_values['gyr_y'] = int(gyr_match.group(2))
                    sensor_values['gyr_z'] = int(gyr_match.group(3))
            
            # Magnetometer
            elif 'MAG_X:' in line:
                mag_match = re.search(r'MAG_X:\s*([+-]?\d+),\s*MAG_Y:\s*([+-]?\d+),\s*MAG_Z:\s*([+-]?\d+)', line)
                if mag_match:
                    sensor_values['mag_x'] = int(mag_match.group(1))
                    sensor_values['mag_y'] = int(mag_match.group(2))
                    sensor_values['mag_z'] = int(mag_match.group(3))
            
            # Quaternion variance or other QVAR
            elif 'QVAR:' in line:
                qvar_match = re.search(r'QVAR:\s*([+-]?\d*\.?\d+)', line)
                if qvar_match:
                    sensor_values['qvar'] = float(qvar_match.group(1))
        
        if sensor_values:
            self.last_sensor_reading = sensor_values
            self.log_raw_sensor_data(timestamp, sensor_values)
            
            # Calculate head pose if we have sufficient data
            if all(key in sensor_values for key in ['acc_x', 'acc_y', 'acc_z', 'gyr_x', 'gyr_y', 'gyr_z']):
                head_pose = self.calculate_head_pose(sensor_values)
                if head_pose:
                    self.log_head_pose_data(timestamp, head_pose)
        
        return sensor_values
    
    def calculate_head_pose(self, sensor_data):
        """Calculate yaw, pitch, roll from accelerometer and gyroscope data"""
        try:
            # Convert accelerometer readings (assuming they're in raw ADC counts)
            # You may need to adjust these scale factors based on your sensor specs
            acc_x = sensor_data['acc_x'] / 1000.0  # Convert to g
            acc_y = sensor_data['acc_y'] / 1000.0
            acc_z = sensor_data['acc_z'] / 1000.0
            
            # Calculate pitch and roll from accelerometer (static orientation)
            pitch = math.atan2(acc_x, math.sqrt(acc_y**2 + acc_z**2))
            roll = math.atan2(acc_y, acc_z)
            
            # Convert to degrees
            pitch_deg = math.degrees(pitch)
            roll_deg = math.degrees(roll)
            
            # For yaw, we need magnetometer data or integration of gyroscope
            # Using a simple approach with gyroscope Z-axis for yaw rate
            gyr_z = sensor_data['gyr_z']
            # This is a simplified approach - in practice, you'd integrate over time
            yaw_deg = gyr_z / 100.0  # Rough conversion, adjust based on your gyro specs
            
            # Create a simple quaternion representation (simplified)
            # In a real implementation, you'd use proper sensor fusion algorithms
            quat_w = math.cos((pitch/2)) * math.cos((roll/2)) * math.cos((yaw_deg*math.pi/180/2))
            quat_x = math.sin((pitch/2)) * math.cos((roll/2)) * math.cos((yaw_deg*math.pi/180/2))
            quat_y = math.cos((pitch/2)) * math.sin((roll/2)) * math.cos((yaw_deg*math.pi/180/2))
            quat_z = math.cos((pitch/2)) * math.cos((roll/2)) * math.sin((yaw_deg*math.pi/180/2))
            
            return {
                'yaw': yaw_deg,
                'pitch': pitch_deg, 
                'roll': roll_deg,
                'quat_w': quat_w,
                'quat_x': quat_x,
                'quat_y': quat_y,
                'quat_z': quat_z
            }
            
        except Exception as e:
            logging.error(f"Error calculating head pose: {e}")
            return None
    
    def detect_ecg_data(self, data):
        """Look for potential ECG data patterns in the serial data"""
        # Look for patterns that might indicate ECG data
        # This is speculative - you'll need to identify actual ECG data format
        
        # Check for specific keywords or patterns
        ascii_data = ""
        try:
            ascii_data = data.decode('ascii', errors='ignore')
        except:
            pass
            
        # Look for ECG-related keywords
        ecg_keywords = ['ECG', 'HEART', 'BPM', 'HR', 'PULSE']
        for keyword in ecg_keywords:
            if keyword in ascii_data.upper():
                # Try to extract numeric value
                ecg_match = re.search(rf'{keyword}[:\s]*([+-]?\d*\.?\d+)', ascii_data, re.IGNORECASE)
                if ecg_match:
                    ecg_value = float(ecg_match.group(1))
                    self.log_ecg_data(datetime.now(), ecg_value, "keyword_detection")
                    return ecg_value
        
        # Look for binary patterns that might be ECG
        if len(data) >= 2:
            # Try to find patterns that look like ECG readings
            # ECG values are typically in the range of 0-1024 for 10-bit ADC
            for i in range(len(data) - 1):
                try:
                    # Try 16-bit unsigned integer
                    value = struct.unpack('<H', data[i:i+2])[0]
                    if 0 < value < 4096:  # Reasonable range for ECG ADC
                        # This could be ECG data - log it
                        self.ecg_buffer.append(value)
                        if len(self.ecg_buffer) > 100:
                            self.ecg_buffer = self.ecg_buffer[-50:]  # Keep recent values
                        
                        # Log every 10th sample to avoid spam
                        if len(self.ecg_buffer) % 10 == 0:
                            avg_value = sum(self.ecg_buffer[-10:]) / 10
                            self.log_ecg_data(datetime.now(), avg_value, "binary_pattern")
                            return avg_value
                except:
                    continue
        
        return None
    
    def log_raw_sensor_data(self, timestamp, sensor_data):
        """Log raw sensor data to CSV"""
        try:
            with open(RAW_SENSOR_CSV, mode='a', newline='') as f:
                writer = csv.writer(f)
                row = [
                    timestamp.isoformat(),
                    sensor_data.get('acc_x', ''),
                    sensor_data.get('acc_y', ''),
                    sensor_data.get('acc_z', ''),
                    sensor_data.get('gyr_x', ''),
                    sensor_data.get('gyr_y', ''),
                    sensor_data.get('gyr_z', ''),
                    sensor_data.get('mag_x', ''),
                    sensor_data.get('mag_y', ''),
                    sensor_data.get('mag_z', ''),
                    sensor_data.get('temp', ''),
                    sensor_data.get('pressure', ''),
                    sensor_data.get('qvar', '')
                ]
                writer.writerow(row)
        except Exception as e:
            logging.error(f"Error logging raw sensor data: {e}")
    
    def log_head_pose_data(self, timestamp, head_pose):
        """Log head pose data to CSV"""
        try:
            with open(HEADPOSE_CSV, mode='a', newline='') as f:
                writer = csv.writer(f)
                writer.writerow([
                    timestamp.isoformat(),
                    head_pose['yaw'],
                    head_pose['pitch'],
                    head_pose['roll'],
                    head_pose['quat_w'],
                    head_pose['quat_x'],
                    head_pose['quat_y'],
                    head_pose['quat_z']
                ])
            
            # Log to console
            logging.info(f"Head Pose - Yaw: {head_pose['yaw']:.2f}°, Pitch: {head_pose['pitch']:.2f}°, Roll: {head_pose['roll']:.2f}°")
            
        except Exception as e:
            logging.error(f"Error logging head pose data: {e}")
    
    def log_ecg_data(self, timestamp, ecg_value, source):
        """Log ECG data to CSV"""
        try:
            # Simple heart rate estimation (very basic)
            heart_rate = self.estimate_heart_rate()
            
            with open(ECG_CSV, mode='a', newline='') as f:
                writer = csv.writer(f)
                writer.writerow([
                    timestamp.isoformat(),
                    ecg_value,
                    heart_rate,
                    source
                ])
            
            logging.info(f"ECG Data - Value: {ecg_value:.2f}, Est. HR: {heart_rate} BPM, Source: {source}")
            
        except Exception as e:
            logging.error(f"Error logging ECG data: {e}")
    
    def estimate_heart_rate(self):
        """Simple heart rate estimation from ECG buffer"""
        if len(self.ecg_buffer) < 20:
            return 0
        
        # Very simple peak detection and rate calculation
        # In practice, you'd use more sophisticated algorithms
        try:
            recent_data = self.ecg_buffer[-20:]
            avg = sum(recent_data) / len(recent_data)
            peaks = sum(1 for i in range(1, len(recent_data)-1) 
                       if recent_data[i] > recent_data[i-1] and 
                          recent_data[i] > recent_data[i+1] and
                          recent_data[i] > avg * 1.1)
            
            # Estimate BPM (this is very rough)
            if peaks > 0:
                return min(peaks * 6, 200)  # Cap at 200 BPM
            return 0
        except:
            return 0
    
    def send_ecg_commands(self):
        """Send commands specifically for ECG data"""
        ecg_commands = [
            b'ECG_START\r\n',
            b'START_ECG\r\n', 
            b'GET_ECG\r\n',
            b'ECG_ON\r\n',
            b'STREAM_ECG\r\n',
            b'HR_ON\r\n',
            b'\x10',  # DLE character - sometimes used for medical devices
            b'\x11',  # Device Control 1
        ]
        
        for cmd in ecg_commands:
            try:
                logging.info(f"Sending ECG command: {cmd}")
                self.serial_conn.write(cmd)
                time.sleep(0.2)
                
                # Check for response
                if self.serial_conn.in_waiting > 0:
                    response = self.serial_conn.read(self.serial_conn.in_waiting)
                    logging.info(f"ECG command response: {response.hex()}")
            except Exception as e:
                logging.error(f"Error sending ECG command {cmd}: {e}")
    
    def monitor_continuous(self):
        """Monitor serial port continuously for both head pose and ECG data"""
        if not self.serial_conn:
            logging.error("Not connected to serial port")
            return
        
        logging.info("Starting continuous monitoring for Head Pose and ECG data...")
        logging.info("Press Ctrl+C to stop")
        
        # Try sending ECG commands
        self.send_ecg_commands()
        time.sleep(1)
        
        try:
            while True:
                if self.serial_conn.in_waiting > 0:
                    # Read available data
                    data = self.serial_conn.read(self.serial_conn.in_waiting)
                    
                    if data:
                        # Try to parse as ASCII sensor data first
                        try:
                            ascii_data = data.decode('ascii', errors='ignore')
                            if any(keyword in ascii_data for keyword in ['ACC_', 'GYR_', 'MAG_', 'TEMP:', 'PRESS:']):
                                self.parse_sensor_data(ascii_data)
                        except:
                            pass
                        
                        # Look for ECG data patterns
                        self.detect_ecg_data(data)
                
                time.sleep(0.01)  # Small delay to prevent CPU overload
                
        except KeyboardInterrupt:
            logging.info("Monitoring stopped by user")

def main():
    # Create monitor instance
    monitor = HeadPoseECGMonitor(SERIAL_PORT, BAUD_RATE, TIMEOUT)
    
    # Connect to serial port
    if monitor.connect():
        try:
            # Start monitoring
            monitor.monitor_continuous()
        finally:
            monitor.disconnect()
    else:
        logging.error("Failed to connect to serial port")

if __name__ == "__main__":
    main()