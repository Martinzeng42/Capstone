import asyncio
from bleak import BleakClient
import logging
import threading
from mac import ADDRESS
from sensortile.sensor_handler import SensorTileHandler
from utils.constants import CHARACTERISTIC_01, CHARACTERISTIC_02, CSV_FILE, SAVE_LOGS
from Scan_Network.scan_network import Scan_Network
from TCP_Object_Detection.StreamYOLOTCP_ServerOnly import StreamYOLOTCPServer
import queue

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("sensortile/logs/sensortile.log"),
        logging.StreamHandler()
    ]
)


async def main():
    ### Scan network
    scan = Scan_Network()
    ips = scan.get_devices_list()
    if not ips:
        logging.error("No device to connect to.")
        # return

    # Shared command queue to communicate between sensorhandler and camerahandler threads

    cmd_queue = queue.Queue()
    cam_data_queue = queue.Queue()
    cam = StreamYOLOTCPServer(cmd_queue=cmd_queue, cam_data_queue=cam_data_queue)
    threading.Thread(target=cam.run, kwargs={"headless": True}, daemon=True).start()

    ### Connect to Sensortile
    handler = SensorTileHandler(ips, scan)
    handler.cmd_queue = cmd_queue   # give sensortile handler the same queue
    handler.cam_data_queue = cam_data_queue
    logging.info("Connecting to SensorTile...")

    async with BleakClient(ADDRESS, timeout=60) as client:
        if not client.is_connected:
            logging.error("Failed to connect to SensorTile.")
            return
        logging.info("Connected to SensorTile.")
        # if SAVE_LOGS:
        for service in client.services:
            for char in service.characteristics:
                logging.info(f"{char.uuid} -> {char.properties}")

        await client.start_notify(CHARACTERISTIC_01, handler.handle_notification)
        await client.start_notify(CHARACTERISTIC_02, handler.handle_notification)

        if SAVE_LOGS:
            logging.info("Sending start command...")
        await client.write_gatt_char(CHARACTERISTIC_02, bytearray([0x32, 0x01, 0x0A]), response=False)

        logging.info("Begin streaming...")
        logging.info("Setup start")
        logging.info("Waiting for double nod gesture to start calibration...")

        try:
            while True:
                await asyncio.sleep(1)
        except KeyboardInterrupt:
            logging.info("Stopping...")

        if SAVE_LOGS:
            handler.save_log(CSV_FILE)

        await client.stop_notify(CHARACTERISTIC_01)
        await client.stop_notify(CHARACTERISTIC_02)

if __name__ == "__main__":
    asyncio.run(main())
