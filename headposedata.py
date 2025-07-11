import asyncio
from bleak import BleakClient

# SensorTile MAC address 
ADDRESS = "F8:47:EE:75:CB:80"      # Change to your specific one

# UUIDs from your console:         # These should be the same for everyone
SERVICE_UUID = "00000000-0004-11e1-9ab4-0002a5d5c51b"
CHARACTERISTIC_01 = "00000001-0004-11e1-ac36-0002a5d5c51b"  # Notify
CHARACTERISTIC_02 = "00000002-0004-11e1-ac36-0002a5d5c51b"  # Notify + Write

# Notification handler
import struct

def notification_handler(sender, data):
    print(f"\nNotification from {sender}:")
    print(f"Hex: {data.hex()}")
    print(f"Length: {len(data)} bytes")

    if len(data) < 10:
        print("Packet too short (likely just ACK or status)")
        return
    elif len(data) >= 60:
        print("Likely sensor data received!")

        # Head Pose starts at byte 9 (indexing from 0), 3 floats (12 bytes total)
        try:
            yaw, pitch, roll = struct.unpack("<fff", data[9:21])
            print(f"Head Pose -> Yaw: {yaw:.2f}, Pitch: {pitch:.2f}, Roll: {roll:.2f}")
        except Exception as e:
            print(f"Error decoding: {e}")


async def main():
    print("Connecting to SensorTile...")
    async with BleakClient(ADDRESS, timeout=60) as client:
        print("Connected to SensorTile")

        # Confirm characteristics
        print("Characteristic properties:")
        for service in client.services:
            for char in service.characteristics:
                print(f"{char.uuid} -> {char.properties}")

        # Subscribe to notifications on both custom characteristics
        await client.start_notify(CHARACTERISTIC_01, notification_handler)
        await client.start_notify(CHARACTERISTIC_02, notification_handler)
        print("Subscribed to both characteristics")

        # Send start stream command: 32 01 0A
        print("Sending start command (32 01 0A)...")
        await client.write_gatt_char(CHARACTERISTIC_02, bytearray([0x32, 0x01, 0x0A]), response=False)
        print("Sent start command (32 01 0A)")

        print("Begin streaming...")

        # Wait and process notifications
        try:
            while True:
                await asyncio.sleep(1)
        except KeyboardInterrupt:
            print("Stopping...")

        # Stop notifications on exit
        await client.stop_notify(CHARACTERISTIC_01)
        await client.stop_notify(CHARACTERISTIC_02)

# Run main loop
asyncio.run(main())