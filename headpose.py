import asyncio
from bleak import BleakClient

# Replace this with your SensorTile MAC address (from "list" scan)
ADDRESS = "F8:47:EE:75:CB:80"  # Example: ALGOB

# UUIDs from your console:
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

        # Head Pose starts at byte 8 (indexing from 0), 3 floats (12 bytes total)
        try:
            yaw, pitch, roll = struct.unpack("<fff", data[8:20])
            print(f"Head Pose -> Yaw: {yaw:.2f}, Pitch: {pitch:.2f}, Roll: {roll:.2f}")
        except Exception as e:
            print(f"Error decoding: {e}")


async def main():
    print("Connecting to SensorTile...")
    async with BleakClient(ADDRESS) as client:
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

        # Send start stream command: 01 32 0A
        print("Sending start command (01 32 0A)...")
        await client.write_gatt_char(CHARACTERISTIC_02, bytearray([0x01, 0x32, 0x0A]), response=False)
        print("Sent start command (01 32 0A)")

        print("If needed, press BT1 on SensorTile to begin streaming...")

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




# import struct
# import asyncio
# from bleak import BleakClient

# # SensorTile MAC address
# ADDRESS = "F8:47:EE:75:CB:80"

# # Correct characteristic (this one receives full data)
# DATA_CHARACTERISTIC = "00000001-0004-11e1-ac36-0002a5d5c51b"
# # Command characteristic (to send 01 32 0A)
# COMMAND_CHARACTERISTIC = "00000002-0004-11e1-ac36-0002a5d5c51b"

# # Handle incoming sensor data

# def handle_notification(sender, data):
#     print(f"\nNotification from {sender}")
#     print(f"Hex: {data.hex()}")
#     print(f"Length: {len(data)} bytes")

#     if len(data) < 61:
#         print("Not a full sensor packet")
#         return

#     # Strip first 9 bytes (header)
#     payload = data[9:]

#     # Unpack floats (13 floats total = 52 bytes)
#     floats = struct.unpack('<13f', payload)

#     yaw, pitch, roll = floats[0:3]
#     acc_x, acc_y, acc_z = floats[3:6]
#     gyro_x, gyro_y, gyro_z = floats[6:9]
#     fusion1, fusion2, fusion3, fusion4 = floats[9:13]

#     print(f"Yaw: {yaw:.2f}, Pitch: {pitch:.2f}, Roll: {roll:.2f}")
#     print(f"Accel: x={acc_x:.2f}, y={acc_y:.2f}, z={acc_z:.2f}")
#     print(f"Gyro: x={gyro_x:.2f}, y={gyro_y:.2f}, z={gyro_z:.2f}")
#     print(f"Fusion: {fusion1:.2f}, {fusion2:.2f}, {fusion3:.2f}, {fusion4:.2f}")


# async def main():
#     print("Connecting to SensorTile...")
#     async with BleakClient(ADDRESS) as client:
#         print("Connected to SensorTile")

#         print("Sending start command...")
#         await client.write_gatt_char(COMMAND_CHARACTERISTIC, bytearray([0x01, 0x32, 0x0A]), response=True)
#         print("Sent start command")

#         print("Subscribing to sensor data characteristic...")
#         await client.start_notify(DATA_CHARACTERISTIC, handle_notification)
#         print("Subscribed to sensor data characteristic")
#         print("✅ Notification handler attached")


#         print("Waiting for data (press BT1 if needed)...")

#         try:
#             while True:
#                 await asyncio.sleep(1)
#         except KeyboardInterrupt:
#             print("Exiting...")

#         await client.stop_notify(DATA_CHARACTERISTIC)

# asyncio.run(main())
