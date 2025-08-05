import asyncio
from bleak import BleakClient
import csv
import logging
import os
import pandas as pd
import struct
from mac import ADDRESS

# UUIDs from your console:         # These should be the same for everyone
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

CSV_FILE = "logs/csv/sensor_data.csv"
CSV_HEADERS = ["timestamp", "yaw", "pitch", "roll", "ecg"]
SAVE_TO_CSV = True

# Ensure CSV directory exists and write headers if file is new
if not os.path.isfile(CSV_FILE):
    os.makedirs(os.path.dirname(CSV_FILE), exist_ok=True)
    with open(CSV_FILE, mode='w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(CSV_HEADERS)
        logging.info(f"CSV file created with headers: {CSV_HEADERS}")
        
# Global dataframe
df = pd.DataFrame(columns=CSV_HEADERS)

def notification_handler(sender, data):
    logging.info(f"\nNotification from {sender}:")
    logging.info(f"Hex: {data.hex()}")
    logging.info(f"Length: {len(data)} bytes")
    global df

    if len(data) >= 20:
        print("Likely sensor data received!")
        try:
            # Head Pose starts at byte 9 (indexing from 0), 3 floats (12 bytes total)
            yaw, pitch, roll = struct.unpack("<fff", data[9:21])
            
            # QVAR/vAFE (ECG) at bytes 61-64
            if len(data) >= 65:
                ecg, = struct.unpack("<f", data[61:65])
            else:
                ecg = float("nan")

            timestamp = pd.Timestamp.now()

            # Log to DataFrame
            new_row = {
                "timestamp": timestamp,
                "yaw": yaw,
                "pitch": pitch,
                "roll": roll,
                "ecg": ecg
            }
            df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
            # Keep last 30 seconds of data
            df = df[df["timestamp"] > timestamp - pd.Timedelta(seconds=30)]
            
            logging.info(f"Head Pose → Yaw: {yaw:.2f}, Pitch: {pitch:.2f}, Roll: {roll:.2f} | ECG: {ecg:.3f}")
            
            # Append to CSV
            if SAVE_TO_CSV:
                with open(CSV_FILE, mode='a', newline='') as f:
                    writer = csv.writer(f)
                    writer.writerow([
                        timestamp.isoformat(),
                        yaw,
                        pitch,
                        roll,
                        ecg
                    ])
        except Exception as e:
            logging.error(f"Error decoding: {e}")

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
                logging.info(f"{char.uuid} → {char.properties}")

        # Subscribe to notifications
        await client.start_notify(CHARACTERISTIC_01, notification_handler)
        await client.start_notify(CHARACTERISTIC_02, notification_handler)
        logging.info("Subscribed to both characteristics.")

        # Send start command (0x32, 0x01, 0x0A)
        logging.info("Sending start command (32 01 0A)...")
        await client.write_gatt_char(CHARACTERISTIC_02, bytearray([0x32, 0x01, 0x0A]), response=False)
        logging.info("Sent start command.")

        logging.info("Begin streaming...")

        # Keep the script alive to process incoming notifications
        try:
            while True:
                await asyncio.sleep(1)
        except KeyboardInterrupt:
            logging.info("Stopping...")

        # Clean up notifications
        await client.stop_notify(CHARACTERISTIC_01)
        await client.stop_notify(CHARACTERISTIC_02)

# Run main loop
asyncio.run(main())
