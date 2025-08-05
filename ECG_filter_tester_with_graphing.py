

#!/usr/bin/env python3
"""
ECG_filter_tester.py

Streams and visualizes vAFE/ECG from SensorTile in real-time with MEMS Studio–matched analog hub settings:
  • AH_BIO_HPF on (0.15 Hz high-pass)
  • AH_BIO_LPF off (no low-pass)
  • Channel ODR = 240 Hz

Applies:
  1. DC removal
  2. Digital high-pass (0.15 Hz)
  3. Notch filter (alias-aware)
  4. Blink (0.5–3 Hz) & roll (0.1–1 Hz) band-pass

Plots:
  • Raw ECG (mV)
  • After HPF (mV)
  • After Notch (mV)
  • Blink signal (mV)
  • Roll signal (mV)

Requires:
  pip install bleak numpy scipy matplotlib

Usage:
  python3 ECG_filter_tester.py
"""

import asyncio
import struct
import logging
from collections import deque

import numpy as np
import matplotlib.pyplot as plt
from scipy.signal import butter, filtfilt, iirnotch
from bleak import BleakClient
from mac import ADDRESS

# ——— CONFIG (MEMS Studio: AH_BIO_HPF on, AH_BIO_LPF off i.e. hardware LPF @318 Hz, ODR=240 Hz) ——————————————————
FS            = 120.0    # sampling rate (Hz) matching ODR
NOTCH_F       = 60.0     # mains notch freq (Hz)
NOTCH_Q       = 30.0     # notch Q
HP_CUTOFF     = 0.15     # high-pass cutoff (Hz) matching AH_BIO_HPF
BLINK_BAND    = (0.5, 3.0)
ROLL_BAND     = (0.1, 1.0)
BUFFER_SEC    = 10        # seconds to buffer
BUFFER_SIZE   = int(FS * BUFFER_SEC)
BLINK_THRESH  = 0.5      # threshold (mV)
ROLL_THRESH   = 0.2      # threshold (mV)

# BLE UUIDs
CHAR_UUID_NOTIFY = "00000001-0004-11e1-ac36-0002a5d5c51b"
CHAR_UUID_WRITE  = "00000002-0004-11e1-ac36-0002a5d5c51b"

# Logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# Circular buffer for ECG (mV)
ecg_buf = deque(maxlen=BUFFER_SIZE)

# ——— PLOTTING SETUP ——————————————————————————————————————————————
plt.ion()
fig, axs = plt.subplots(5, 1, figsize=(8, 10), sharex=True)
lines = []
titles = ["Raw ECG (mV)", "HPF 0.15 Hz", "Notch", "Blink (0.5–3 Hz)", "Roll (0.1–1 Hz)"]
for ax, title in zip(axs, titles):
    ln, = ax.plot([], [])
    ax.set_title(title)
    ax.grid(True)
    lines.append(ln)
time_axis = np.linspace(-BUFFER_SEC, 0, BUFFER_SIZE)
plt.tight_layout()

# ——— FILTER UTILITIES ———————————————————————————————————————

def _butter(order, cutoff, fs, btype, band=None):
    nyq = 0.5 * fs
    if btype == 'band':
        wb = [band[0]/nyq, band[1]/nyq]
    else:
        wb = cutoff/nyq
    return butter(order, wb, btype=btype)


def apply_highpass(x):
    b, a = _butter(4, HP_CUTOFF, FS, 'high')
    return filtfilt(b, a, x)


def apply_notch(x):
    nyq = FS/2
    f0 = NOTCH_F
    if f0 >= nyq:
        m = f0 % FS
        f0u = FS - m if m > nyq else m
        logging.info(f"Aliasing notch: {f0}Hz→{f0u:.2f}Hz")
    else:
        f0u = f0
    w0 = f0u/nyq
    b, a = iirnotch(w0, NOTCH_Q)
    return filtfilt(b, a, x)


def apply_bandpass(x, band):
    b, a = _butter(4, None, FS, 'band', band)
    return filtfilt(b, a, x)

# buffers for plotting data per stage
plot_bufs = [deque(maxlen=BUFFER_SIZE) for _ in range(5)]

# ——— DATA CALLBACK —————————————————————————————————————————————————

def notification_handler(sender, data: bytearray):
    if len(data) < 65:
        return
    # unpack float (V) → mV
    v = struct.unpack('<f', data[61:65])[0] 
    # DC removal
    centered = v - (np.mean(ecg_buf) if ecg_buf else 0)
    ecg_buf.append(centered)
    if len(ecg_buf) < BUFFER_SIZE:
        return

    arr = np.array(ecg_buf)
    hp = apply_highpass(arr)
    nt = apply_notch(hp)
    bl = apply_bandpass(nt, BLINK_BAND)
    rl = apply_bandpass(nt, ROLL_BAND)

    stages = [arr[-1], hp[-1], nt[-1], bl[-1], rl[-1]]
    # update plot buffers & redraw
    for buf, val in zip(plot_bufs, stages):
        buf.append(val)
    for ln, buf in zip(lines, plot_bufs):
        y = list(buf)
        x = time_axis[-len(y):]
        ln.set_data(x, y); ln.axes.relim(); ln.axes.autoscale_view()
    plt.draw(); plt.pause(0.001)

    # detect events
    if bl[-1] > BLINK_THRESH:
        logging.info("Blink detected!")
    if abs(rl[-1]) > ROLL_THRESH:
        logging.info("Eye roll detected!")

# ——— MAIN ——————————————————————————————————————————————

async def main():
    logging.info("Connecting…")
    async with BleakClient(ADDRESS, timeout=30) as cl:
        if not cl.is_connected:
            logging.error("Connection failed.")
            return
        logging.info("Connected.")
        await cl.start_notify(CHAR_UUID_NOTIFY, notification_handler)
        await cl.start_notify(CHAR_UUID_WRITE, notification_handler)
        # trigger streaming
        await cl.write_gatt_char(CHAR_UUID_WRITE, bytearray([0x32,0x01,0x0A]), response=False)
        logging.info("Streaming at 240 Hz… Ctrl+C to stop.")
        try:
            while True:
                await asyncio.sleep(1)
        except KeyboardInterrupt:
            logging.info("Stopping.")
        finally:
            await cl.stop_notify(CHAR_UUID_NOTIFY)
            await cl.stop_notify(CHAR_UUID_WRITE)

if __name__ == '__main__':
    asyncio.run(main())


