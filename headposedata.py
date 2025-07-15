import asyncio
from bleak import BleakClient
import csv
import logging
import os
import pandas as pd
import struct

# SensorTile MAC address 
ADDRESS = "e4:71:f8:94:a2:aa"      # Change to your specific one

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
CSV_HEADERS = ["timestamp", "yaw", "pitch", "roll"]
SAVE_TO_CSV = True

if not os.path.isfile(CSV_FILE):
    with open(CSV_FILE, mode='w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(CSV_HEADERS)
        logging.info(f"CSV file created with headers: {CSV_HEADERS}")
        
# Global dataframe
df = pd.DataFrame(columns=["timestamp", "yaw", "pitch", "roll"])

def notification_handler(sender, data):
    logging.info(f"\nNotification from {sender}:")
    logging.info(f"Hex: {data.hex()}")
    logging.info(f"Length: {len(data)} bytes")
    global df

    if len(data) < 10:
        logging.warning("Packet too short (likely just ACK or status)")
        return
    elif len(data) >= 60:
        logging.info("Likely sensor data received!")

        # Head Pose starts at byte 9 (indexing from 0), 3 floats (12 bytes total)
        try:
            yaw, pitch, roll = struct.unpack("<fff", data[9:21])
            timestamp = pd.Timestamp.now()

            # Log to DataFrame
            new_row = {"timestamp": timestamp, "yaw": yaw, "pitch": pitch, "roll": roll}
            df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
            # Keep last 30 seconds of data
            df = df[df["timestamp"] > timestamp - pd.Timedelta(seconds=30)]
            
            logging.info(f"Head Pose -> Yaw: {yaw:.2f}, Pitch: {pitch:.2f}, Roll: {roll:.2f}")
            # Append to csv
            if SAVE_TO_CSV:
                with open(CSV_FILE, mode='a', newline='') as f:
                    writer = csv.writer(f)
                    writer.writerow([timestamp.isoformat(), yaw, pitch, roll])
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
                logging.info(f"{char.uuid} -> {char.properties}")

        await client.start_notify(CHARACTERISTIC_01, notification_handler)
        await client.start_notify(CHARACTERISTIC_02, notification_handler)
        logging.info("Subscribed to both characteristics.")

        logging.info("Sending start command (32 01 0A)...")
        await client.write_gatt_char(CHARACTERISTIC_02, bytearray([0x32, 0x01, 0x0A]), response=False)
        logging.info("Sent start command (32 01 0A)")

        logging.info("Begin streaming...")

        # Wait and process notifications
        try:
            while True:
                await asyncio.sleep(1)
        except KeyboardInterrupt:
            logging.info("Stopping...")

        # Stop notifications on exit
        await client.stop_notify(CHARACTERISTIC_01)
        await client.stop_notify(CHARACTERISTIC_02)

# Run main loop
asyncio.run(main())