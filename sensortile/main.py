import asyncio
from bleak import BleakClient
import logging
from mac import ADDRESS
from sensortile.sensor_handler import SensorTileHandler
from utils.constants import CHARACTERISTIC_01, CHARACTERISTIC_02, CSV_FILE, SAVE_LOGS

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
    handler = SensorTileHandler()
    logging.info("Connecting to SensorTile...")

    async with BleakClient(ADDRESS, timeout=60) as client:
        if not client.is_connected:
            logging.error("Failed to connect to SensorTile.")
            return
        logging.info("Connected to SensorTile.")
        if SAVE_LOGS:
            for service in client.services:
                for char in service.characteristics:
                    logging.info(f"{char.uuid} -> {char.properties}")

        await client.start_notify(CHARACTERISTIC_01, handler.handle_notification)
        await client.start_notify(CHARACTERISTIC_02, handler.handle_notification)

        if SAVE_LOGS:
            logging.info("Sending start command...")
        await client.write_gatt_char(CHARACTERISTIC_02, bytearray([0x32, 0x01, 0x0A]), response=False)

        logging.info("Begin streaming...")

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
