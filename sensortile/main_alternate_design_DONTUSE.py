import asyncio
import threading
import queue
import time
import logging
import pandas as pd
import struct
import numpy as np
from scipy.spatial.transform import Rotation as R

from sensortile.movement_detection import detect_nod, detect_roll
from utils.constants import (
    NOD_MIN_AMPLITUDE,
    ROLL_MIN_AMPLITUDE,
    CSV_HEADERS,
    NOD_TIME_WINDOW,
    NOD_COOLDOWN,
    SAVE_LOGS,
    CHARACTERISTIC_01,
    CHARACTERISTIC_02,
    CSV_FILE
)
from Scan_Network.scan_network import Scan_Network
from sensortile.sensor_handler import SensorTileHandler
from mac import ADDRESS

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("sensortile/logs/sensortile.log"),
        logging.StreamHandler()
    ]
)

# Queues for data passing
sensortile_queue = queue.Queue(maxsize=100)
camera_frame_queue = queue.Queue(maxsize=50)

camera_streaming = threading.Event()

def scan_network_once():
    scan = Scan_Network()
    ips = scan.get_devices_list()
    if ips:
        logging.info(f"Network scan found devices: {ips}")
    else:
        logging.warning("Network scan found no devices.")
    return ips

# Sensortile BLE Producer: runs BLE client inside a thread
def sensortile_producer():
    async def ble_main():
        # Note: No network scan here — we already have ADDRESS (MAC) known

        # Create dummy scan and ips just for SensorTileHandler compatibility
        scan = None
        ips = [ADDRESS]      #! just pass in connected_devices from __main__ as an argument to sensortile_producer(). tho even letting sensortile_producer handle it would be fine. Tho you'd have less flexibility with what u can do with it in the main file, like referencing it again later or smth. On that note, need the calibration step to return the calibrated list or smth. Try integrating using the existing main.py first.

        # handler = SensorTileHandler(ips, scan, data_queue=sensortile_queue)
        handler = SensorTileHandler(ips, scan)
        logging.info("Connecting to SensorTile...")

        from bleak import BleakClient

        async with BleakClient(ADDRESS, timeout=60) as client:
            if not client.is_connected:
                logging.error("Failed to connect to SensorTile.")
                return
            logging.info("Connected to SensorTile.")

            # Log characteristics (optional)
            for service in client.services:
                for char in service.characteristics:
                    logging.info(f"{char.uuid} -> {char.properties}")

            await client.start_notify(CHARACTERISTIC_01, handler.handle_notification)
            await client.start_notify(CHARACTERISTIC_02, handler.handle_notification)

            if SAVE_LOGS:
                logging.info("Sending start command...")
            await client.write_gatt_char(CHARACTERISTIC_02, bytearray([0x32, 0x01, 0x0A]), response=False)

            logging.info("Begin streaming... Setup start")

            try:
                while True:
                    await asyncio.sleep(1)
            except asyncio.CancelledError:
                logging.info("BLE task cancelled, stopping notifications")
            finally:
                if SAVE_LOGS:
                    handler.save_log(CSV_FILE)

                await client.stop_notify(CHARACTERISTIC_01)
                await client.stop_notify(CHARACTERISTIC_02)

    asyncio.run(ble_main())

# Camera producer thread
def camera_producer():
    while True:
        camera_streaming.wait()
        frames = capture_camera_frames()
        for frame in frames:
            try:
                camera_frame_queue.put(frame, timeout=0.1)
            except queue.Full:
                pass

# Main consumer thread
def main_consumer():
    buffer = pd.DataFrame(columns=["timestamp", "yaw", "pitch", "roll", "vafe", "a_lin_x", "a_lin_y", "a_lin_z"])
    BUFFER_MAX = 20

    while True:
        try:
            sens_data = sensortile_queue.get(timeout=0.1)
        except queue.Empty:
            sens_data = None

        if sens_data is not None:
            buffer = pd.concat([buffer, pd.DataFrame([sens_data])], ignore_index=True)
            if len(buffer) > BUFFER_MAX:
                buffer = buffer.iloc[-BUFFER_MAX:]

            if detect_gesture(buffer):
                if not camera_streaming.is_set():
                    logging.info("Partial gesture detected: starting camera stream")
                    camera_streaming.set()

            if gesture_confirmed(buffer):
                logging.info("Gesture confirmed: processing frames")
                frames = drain_camera_frames()
                process_frames(frames)
                camera_streaming.clear()

        time.sleep(0.01)

# Helper functions
def drain_camera_frames():
    frames = []
    while True:
        try:
            frame = camera_frame_queue.get_nowait()
            frames.append(frame)
        except queue.Empty:
            break
    return frames

# Placeholders: fill with your actual implementations
def capture_camera_frames():
    # Capture a batch of frames from the camera stream
    return []

def detect_gesture(buffer):
    return detect_nod(buffer, NOD_MIN_AMPLITUDE) or detect_roll(buffer, ROLL_MIN_AMPLITUDE)

def gesture_confirmed(buffer):
    return detect_nod(buffer, NOD_MIN_AMPLITUDE)

def process_frames(frames):
    logging.info(f"Processing {len(frames)} frames")

if __name__ == "__main__":
    # Scan network once at startup (optional, for your other smart devices)
    # For faster testing, can comment out and hard code
    connected_devices = scan_network_once()

    if not connected_devices:
        logging.info("no connected devices womp womp")
        # could exit program here

    # Start producer threads
    threading.Thread(target=sensortile_producer, daemon=True).start()
    threading.Thread(target=camera_producer, daemon=True).start()

    # Run consumer loop in main thread
    try:
        main_consumer()
    except KeyboardInterrupt:
        logging.info("Exiting...")
