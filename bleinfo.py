from bleak import BleakClient
import asyncio

address = "F8:47:EE:75:CB:80"  # Your SensorTile MAC

async def main():
    print("Connecting...")
    async with BleakClient(address, timeout=20.0) as client:
        print("Connected!")

        for service in client.services:
            print(f"Service: {service.uuid}")
            for char in service.characteristics:
                print(f"  Characteristic: {char.uuid}, Notify: {'notify' in char.properties}")

asyncio.run(main())
