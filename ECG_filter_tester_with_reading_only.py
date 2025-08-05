#!/usr/bin/env python3
"""
ECG_filter_tester.py

Streams vAFE/ECG from SensorTile, applies alias‑aware notch + blink/roll filters,
logs raw and filtered values, and detects blinks/eye rolls.
"""

import asyncio
import struct
import logging
from collections import deque

import numpy as np
from scipy.signal import butter, filtfilt, iirnotch
from bleak import BleakClient
from mac import ADDRESS  # your SensorTile BLE address

# ——— CONFIG ——————————————————————————————————————————————
# FS           = 50.0    # vAFE sampling rate (Hz)
# NOTCH_F      = 60.0    # mains frequency to notch out (Hz)
# NOTCH_Q      = 30.0    # notch quality factor
# BLINK_BAND   = (0.5, 3.0)
# ROLL_BAND    = (0.1, 1.0)
# BUFFER_SEC   = 5       # seconds of data to buffer
# BUFFER_SIZE  = int(FS * BUFFER_SEC)
# BLINK_THRESH = 0.5
# ROLL_THRESH  = 0.2

FS            = 240.0    # sampling rate (Hz) matching ODR
NOTCH_F       = 60.0     # mains notch freq (Hz)
NOTCH_Q       = 30.0     # notch Q
HP_CUTOFF     = 0.15     # high-pass cutoff (Hz) matching AH_BIO_HPF
BLINK_BAND    = (0.5, 3.0)
ROLL_BAND     = (0.1, 1.0)
BUFFER_SEC    = 0.2        # seconds to buffer
BUFFER_SIZE   = int(FS * BUFFER_SEC)
BLINK_THRESH  = 0.5      # threshold (mV)
ROLL_THRESH   = 0.2      # threshold (mV)

# BLE UUIDs
CHAR_UUID_NOTIFY = "00000001-0004-11e1-ac36-0002a5d5c51b"
CHAR_UUID_WRITE  = "00000002-0004-11e1-ac36-0002a5d5c51b"

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)

# Buffer for ECG samples
ecg_buffer = deque(maxlen=BUFFER_SIZE)

# ——— FILTER UTILITIES ———————————————————————————————————————

def butter_bandpass(lowcut, highcut, fs, order=4):
    nyq = 0.5 * fs
    b, a = butter(order, [lowcut/nyq, highcut/nyq], btype='band')
    return b, a


def apply_bandpass(data, lowcut, highcut, fs, order=4):
    b, a = butter_bandpass(lowcut, highcut, fs, order)
    return filtfilt(b, a, data)


def apply_notch(data, fs, f0=NOTCH_F, Q=NOTCH_Q):
    """
    Alias-aware notch: if f0 > Nyquist (fs/2), alias into band before designing filter.
    """
    nyq = fs / 2
    # alias mains freq if above Nyquist
    if f0 >= nyq:
        # map f0 into [0, fs) then reflect into [0, nyq]
        f_mod = f0 % fs
        if f_mod > nyq:
            f0_use = fs - f_mod
        else:
            f0_use = f_mod
        logging.info(f"Notch frequency {f0}Hz > Nyquist {nyq}Hz; aliased to {f0_use:.2f}Hz")
    else:
        f0_use = f0

    # normalized notch freq for iirnotch
    w0 = f0_use / nyq
    b, a = iirnotch(w0, Q)
    return filtfilt(b, a, data)

# ——— HANDLER —————————————————————————————————————————————————

def notification_handler(sender, data: bytearray):
    # Ensure full packet
    if len(data) < 65:
        return
    # Unpack raw ECG (bytes 61–64)
    ecg_raw, = struct.unpack('<f', data[61:65])
    # Buffer sample
    ecg_buffer.append(ecg_raw)

    # Only process when buffer full
    if len(ecg_buffer) < BUFFER_SIZE:
        return

    arr = np.array(ecg_buffer)
    # Notch + bandpass
    ecg_notch = apply_notch(arr, FS, NOTCH_F, NOTCH_Q)
    blink_sig = apply_bandpass(ecg_notch, *BLINK_BAND, FS)
    roll_sig  = apply_bandpass(ecg_notch, *ROLL_BAND, FS)

    # Latest values
    raw   = arr[-1]
    notch = ecg_notch[-1]
    blink = blink_sig[-1]
    roll  = roll_sig[-1]

    # Log
    logging.info(
        f"ECG raw={raw:.3f} | notch={notch:.3f} | "
        f"blink={blink:.3f} | roll={roll:.3f}"
    )

    # Event detection
    if blink > BLINK_THRESH:
        logging.info("Blink detected!")
    if abs(roll) > ROLL_THRESH:
        logging.info("Eye roll detected!")

# ——— MAIN LOOP ——————————————————————————————————————————————

async def main():
    logging.info("Connecting to SensorTile…")
    async with BleakClient(ADDRESS, timeout=30.0) as client:
        if not client.is_connected:
            logging.error("Failed to connect to SensorTile.")
            return
        logging.info("Connected.")

        # Subscribe
        await client.start_notify(CHAR_UUID_NOTIFY, notification_handler)
        await client.start_notify(CHAR_UUID_WRITE, notification_handler)
        # Start vAFE stream
        await client.write_gatt_char(CHAR_UUID_WRITE, bytearray([0x32,0x01,0x0A]), response=False)
        logging.info("Streaming ECG with filters—Ctrl+C to stop.")

        try:
            while True:
                await asyncio.sleep(1)
        except KeyboardInterrupt:
            logging.info("Stopping stream.")
        finally:
            await client.stop_notify(CHAR_UUID_NOTIFY)
            await client.stop_notify(CHAR_UUID_WRITE)

if __name__ == '__main__':
    asyncio.run(main())
