import asyncio
import struct
import logging
from bleak import BleakClient
from enum import Enum
import argparse
import time
import csv

# Attempt to import gpiozero LED; provide a dummy fallback for non-RPi environments
try:
    from gpiozero import LED
except ImportError:
    logging.warning("gpiozero not found; using dummy LED for testing")
    class LED:
        def __init__(self, pin):
            self.pin = pin
            print(f"[MOCK LED] Initialized on pin {pin}")
        def on(self):
            print(f"[MOCK LED] ON (pin {self.pin})")
        def off(self):
            print(f"[MOCK LED] OFF (pin {self.pin})")

from mac import ADDRESS  # Your SensorTile BLE address

# BLE UUIDs
SERVICE_UUID = "00000000-0004-11e1-9ab4-0002a5d5c51b"
CHARACTERISTIC_01 = "00000001-0004-11e1-ac36-0002a5d5c51b"  # Notify
CHARACTERISTIC_02 = "00000002-0004-11e1-ac36-0002a5d5c51b"  # Notify + Write

# LED setup: connect LED + resistor to GPIO17 and LED - to GND (pin number is ignored by dummy)
led = LED(17)

# Gesture thresholds (degrees)
NOD_THRESHOLD = 15.0    # pitch relative change to detect "nod"
SHAKE_THRESHOLD = 15.0  # yaw relative change to detect "shake"

# State machine for selection vs gesture
class State(Enum):
    SELECTING = 0  # waiting for object selection
    GESTURING = 1  # ready for gesture commands

current_state = State.SELECTING
baseline_yaw = 0.0
baseline_pitch = 0.0

# Debounce flags
yaw_neutral = True
pitch_neutral = True

# CSV path for simulation (from headpose data logger)
CSV_FILE = "logs/csv/sensor_data.csv"

# Logging setup
tlogging = logging.getLogger()
tlogging.setLevel(logging.INFO)
formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
file_handler = logging.FileHandler("logs/gesture_control.log")
file_handler.setFormatter(formatter)
stream_handler = logging.StreamHandler()
stream_handler.setFormatter(formatter)
tlogging.addHandler(file_handler)
tlogging.addHandler(stream_handler)


def on_object_selected(yaw, pitch):
    """
    Call this when your ESP32-CAM confirms object selection or for simulation.
    Records current headpose as baseline for gesture detection.
    """
    global baseline_yaw, baseline_pitch, current_state, yaw_neutral, pitch_neutral
    baseline_yaw = yaw
    baseline_pitch = pitch
    yaw_neutral = True
    pitch_neutral = True
    current_state = State.GESTURING
    tlogging.info(f"Object selected at yaw={yaw:.2f}, pitch={pitch:.2f}. Enter GESTURING state.")
    print("Selection locked; awaiting gesture...")


def reset_to_selecting():
    """Return to selection mode after a gesture is processed."""
    global current_state
    current_state = State.SELECTING
    tlogging.info("Returning to SELECTING state.")
    print("Gesture done; return to selection.")


def gesture_detection(yaw, pitch):
    """
    Detect nod/shake relative to baseline when in GESTURING.
    Nod -> LED ON; Shake -> LED OFF; then reset.
    """
    global yaw_neutral, pitch_neutral
    dyaw = yaw - baseline_yaw
    dpitch = pitch - baseline_pitch

    if current_state != State.GESTURING:
        return

    # Nod
    if pitch_neutral and dpitch > NOD_THRESHOLD:
        tlogging.info(f"Nod detected (Δpitch={dpitch:.2f}°) -> LED ON")
        print("Nod detected: turning LED ON")
        led.on()
        pitch_neutral = False
        reset_to_selecting()
        return
    elif not pitch_neutral and dpitch < NOD_THRESHOLD * 0.5:
        pitch_neutral = True

    # Shake
    if yaw_neutral and abs(dyaw) > SHAKE_THRESHOLD:
        tlogging.info(f"Shake detected (Δyaw={dyaw:.2f}°) -> LED OFF")
        print("Shake detected: turning LED OFF")
        led.off()
        yaw_neutral = False
        reset_to_selecting()
        return
    elif not yaw_neutral and abs(dyaw) < SHAKE_THRESHOLD * 0.5:
        yaw_neutral = True


def notification_handler(sender, data):
    # Parse headpose floats at bytes 9-21
    if len(data) >= 21:
        try:
            yaw, pitch, roll = struct.unpack("<fff", data[9:21])
            tlogging.info(f"Headpose: yaw={yaw:.2f}, pitch={pitch:.2f}, roll={roll:.2f}")
            gesture_detection(yaw, pitch)
        except struct.error as e:
            tlogging.error(f"Unpack error: {e}")


async def live_mode():
    """Run live BLE streaming and gesture detection."""
    tlogging.info("Connecting to SensorTile...")
    async with BleakClient(ADDRESS, timeout=60) as client:
        if not client.is_connected:
            tlogging.error("Connection failed.")
            return
        tlogging.info("Connected to SensorTile.")

        # Subscribe and start stream
        await client.start_notify(CHARACTERISTIC_01, notification_handler)
        await client.start_notify(CHARACTERISTIC_02, notification_handler)
        await client.write_gatt_char(CHARACTERISTIC_02, bytearray([0x32,0x01,0x0A]), response=False)

        try:
            while True:
                await asyncio.sleep(0.1)
        except KeyboardInterrupt:
            tlogging.info("Stopping live mode...")

        await client.stop_notify(CHARACTERISTIC_01)
        await client.stop_notify(CHARACTERISTIC_02)


def simulate_mode():
    """Load recorded CSV headpose data and simulate selection+gestures."""
    # Load CSV
    try:
        with open(CSV_FILE, newline='') as f:
            reader = csv.DictReader(f)
            rows = list(reader)
    except FileNotFoundError:
        print(f"CSV file not found: {CSV_FILE}")
        return

    if not rows:
        print("No data in CSV to simulate.")
        return

    # Use first row as selection baseline
    yaw0 = float(rows[0]['yaw'])
    pitch0 = float(rows[0]['pitch'])
    on_object_selected(yaw0, pitch0)

    # Feed subsequent data
    for r in rows[1:]:
        yaw = float(r['yaw'])
        pitch = float(r['pitch'])
        gesture_detection(yaw, pitch)
        time.sleep(0.1)
    print("Simulation complete.")


def main():
    parser = argparse.ArgumentParser(description="Headpose LED control with live or simulated data.")
    parser.add_argument('--simulate', action='store_true', help='Run in simulation mode using CSV data')
    args = parser.parse_args()

    if args.simulate:
        simulate_mode()
    else:
        asyncio.run(live_mode())


if __name__ == "__main__":
    main()

# Notes:
# - For live mode, install dependencies: pip3 install bleak gpiozero
# - On non-RPi, gpiozero falls back to dummy LED that prints to console
# - For simulation, ensure you have run headposedata.py to generate logs/csv/sensor_data.csv
