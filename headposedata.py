import asyncio
from bleak import BleakClient
import logging
import os
import pandas as pd
import struct
from mac import ADDRESS
from nod_detection import detect_nod
from utils.constants import *

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("logs/sensortile.log"),
        logging.StreamHandler()
    ]
)

recent_yaw = []       # buffer of (timestamp, yaw)
df = pd.DataFrame(columns=CSV_HEADERS)
last_nod_time = None
NOD_COOLDOWN = pd.Timedelta(seconds=1.0)

def notification_handler(sender, data):
    #logging.info(f"\nNotification from {sender}:")
    #logging.info(f"Hex: {data.hex()}")
    #logging.info(f"Length: {len(data)} bytes")
    global df, last_nod_time

    if len(data) >= 20:
        try:
            yaw, pitch, roll = struct.unpack("<fff", data[9:21])
            timestamp = pd.Timestamp.now()

            new_row = {"timestamp": timestamp, "yaw": yaw, "pitch": pitch, "roll": roll}
            df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
            df = df[df["timestamp"] > timestamp - pd.Timedelta(seconds=NOD_TIME_WINDOW)]

            #logging.info(f"Head Pose -> Yaw: {yaw:.2f}, Pitch: {pitch:.2f}, Roll: {roll:.2f}")

            # Detect nod
            if detect_nod(df, NOD_MIN_AMPLITUDE) and (last_nod_time is None or (timestamp - last_nod_time) > NOD_COOLDOWN):
                logging.info("Nod detected!")
                last_nod_time = timestamp  # Reset cooldown

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
            
        if SAVE_LOGS:
            os.makedirs(os.path.dirname(CSV_FILE), exist_ok=True)
            df.to_csv(CSV_FILE, index=False)
            logging.info(f"Saved log to {CSV_FILE}")

        # Stop notifications on exit
        await client.stop_notify(CHARACTERISTIC_01)
        await client.stop_notify(CHARACTERISTIC_02)

# Run main loop
asyncio.run(main())