#!/usr/bin/env python3
"""
ECG_filter_tester.py

Streams and visualizes vAFE/ECG and IMU from SensorTile, matching MEMS Studio:
  • vAFE[LSB] (raw digital code)
  • ECG [mV] (code→mV via hardware gain)
  • Digital filters: HPF (0.15 Hz), notch (60 Hz alias‑aware)
  • Adaptive LMS motion artifact removal (using accel mag)
  • Blink/roll band‑pass (with error handling on short data)
  • Heart rate estimation (BPM)

Plots (auto-scaled):
  1. vAFE [LSB]
  2. ECG [mV]
  3. After HPF (mV)
  4. After Notch (mV)
  5. Adaptive (mV)
  6. Blink (mV)
  7. Roll (mV)

Requires:
  pip install bleak numpy scipy matplotlib

Usage:
  python3 ECG_filter_tester.py
"""

import asyncio
import struct
import logging
import time
from collections import deque

import numpy as np
import matplotlib.pyplot as plt
from scipy.signal import butter, filtfilt, iirnotch
from bleak import BleakClient
from mac import ADDRESS

# ——— MEMS Studio Params —————————————————————————————————————
FS            = 240.0    # ODR = 240 Hz
HPF_CUTOFF    = 0.15     # AH_BIO_HPF bit → 0.15 Hz
NOTCH_F       = 60.0     # 60 Hz mains
NOTCH_Q       = 30.0     # notch Q
ECG_GAIN      = 80.0     # board gain (LSB→mV)
BLINK_BAND    = (0.5, 3.0)
ROLL_BAND     = (0.1, 1.0)
QRS_BAND      = (5.0, 15.0)  # band for QRS complex detection
QRS_THRESH    = 0.5         # threshold for QRS peak detection
BUFFER_SEC    = 5           # buffer length (sec)
BUFFER_SIZE   = int(FS * BUFFER_SEC)
BLINK_THRESH  = 0.5         # mV threshold
ROLL_THRESH   = 0.2         # mV threshold
LMS_MU        = 0.001       # LMS step size

# BLE UUIDs
CHAR_NOTIFY = "00000001-0004-11e1-ac36-0002a5d5c51b"
CHAR_WRITE  = "00000002-0004-11e1-ac36-0002a5d5c51b"

# Logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# Buffers for stages
ecg_lsb_buf  = deque(maxlen=BUFFER_SIZE)
ecg_mV_buf   = deque(maxlen=BUFFER_SIZE)
hpf_buf      = deque(maxlen=BUFFER_SIZE)
notch_buf    = deque(maxlen=BUFFER_SIZE)
adapt_buf    = deque(maxlen=BUFFER_SIZE)
blink_buf    = deque(maxlen=BUFFER_SIZE)
roll_buf     = deque(maxlen=BUFFER_SIZE)

# For heart rate detection: QRS-based intervals
rr_intervals = deque(maxlen=10)
prev_peak_t  = None  # legacy, no longer used

# LMS weight initialization
prev_peak_t  = None

# LMS weight initialization
w_lms = 0.0

# Live plot setup
plt.ion()
fig, axs = plt.subplots(7, 1, figsize=(8, 12), sharex=True)
titles = [
    "vAFE [LSB]",
    "ECG [mV]",
    "HPF (0.15Hz)",
    "Notch",
    "Adaptive",
    "Blink (0.5–3Hz)",
    "Roll (0.1–1Hz)"
]
lines = []
for ax, title in zip(axs, titles):
    ln, = ax.plot([], [])
    ax.set_title(title)
    ax.grid(True)
    lines.append(ln)

time_axis = np.linspace(-BUFFER_SEC, 0, BUFFER_SIZE)
plt.tight_layout()

# Filter utility functions
def butter_hp(cutoff, fs, order=4):
    b, a = butter(order, cutoff/(0.5*fs), btype='high')
    return b, a

def butter_bp(low, high, fs, order=4):
    b, a = butter(order, [low/(0.5*fs), high/(0.5*fs)], btype='band')
    return b, a

def apply_highpass(x):
    b, a = butter_hp(HPF_CUTOFF, FS)
    return filtfilt(b, a, x)

def apply_notch(x):
    nyq = FS/2
    f0u = NOTCH_F % FS
    if f0u > nyq:
        f0u = FS - f0u
    w0 = f0u/nyq
    b, a = iirnotch(w0, NOTCH_Q)
    return filtfilt(b, a, x)

def apply_bandpass(x, band):
    """
    Band-pass filter with internal exception handling if input too short.
    """
    b, a = butter_bp(band[0], band[1], FS)
    try:
        return filtfilt(b, a, x)
    except ValueError:
        # Skip filtering until buffer is long enough
        logging.warning(
            f"Bandpass skipped: input length {len(x)} < required pad length"
        )
        return np.zeros_like(x)

# Peak detection# Peak detection
def detect_peaks(sig, thresh):
    # rising edge detection
    idx = np.where((sig[:-1] < thresh) & (sig[1:] >= thresh))[0]
    return idx

# Notification handler

def notification_handler(_, data):
    if len(data) < 65:
        return

    # Parse Head Pose (offset 0, bytes 9-20)
    try:
        yaw, pitch, roll = struct.unpack('<fff', data[9:21])
    except struct.error:
        yaw = pitch = roll = 0.0
    logging.info(f"Head Pose → Yaw: {yaw:.2f}, Pitch: {pitch:.2f}, Roll: {roll:.2f}")

    # Parse Acceleration (bytes 21-32)
    ax, ay, az = struct.unpack('<fff', data[21:33])
    accel_mag = np.sqrt(ax*ax + ay*ay + az*az)
    logging.info(f"Accel → X: {ax:.2f}, Y: {ay:.2f}, Z: {az:.2f}")

    # Parse Angular Rate (bytes 33-44)
    try:
        gx, gy, gz = struct.unpack('<fff', data[33:45])
    except struct.error:
        gx = gy = gz = 0.0
    logging.info(f"Gyro → X: {gx:.2f}, Y: {gy:.2f}, Z: {gz:.2f}")

    # Parse Sensor Fusion quaternion (bytes 45-60)
    try:
        q0, q1, q2, q3 = struct.unpack('<ffff', data[45:61])
    except struct.error:
        q0 = q1 = q2 = q3 = 0.0
    logging.info(f"Quat → q0: {q0:.2f}, q1: {q1:.2f}, q2: {q2:.2f}, q3: {q3:.2f}")

    # Parse QVAR/vAFE (bytes 61-64)
    qvar = struct.unpack('<f', data[61:65])[0]
    logging.info(f"QVAR/vAFE: {qvar:.3f}")

    if len(data) < 65:
        return

    # 1) Raw vAFE LSB
    lsb = struct.unpack('<f', data[61:65])[0]
    ecg_lsb_buf.append(lsb)

    # 2) Convert to mV
    mV = lsb * 1000.0 / ECG_GAIN
    ecg_mV_buf.append(mV)

    # 3) IMU accel magnitude
    ax, ay, az = struct.unpack('<fff', data[21:33])
    accel_mag = np.sqrt(ax*ax + ay*ay + az*az)

    # wait until buffer full
    if len(ecg_mV_buf) < BUFFER_SIZE:
        return

    # arrays for processing
    ecg_arr = np.array(ecg_mV_buf)

    # 4) High-pass
    hpf = apply_highpass(ecg_arr)
    hpf_buf.append(hpf[-1])

    # 5) Notch
    nt = apply_notch(hpf)
    notch_buf.append(nt[-1])

    # 6) LMS adaptive artifact removal
    global w_lms, prev_peak_t
    err = nt[-1] - w_lms * accel_mag
    w_lms += LMS_MU * err * accel_mag
    adapt_buf.append(err)

    # 7) Blink & roll band-pass with error handling
    adapt_arr = np.array(adapt_buf)
    try:
        bl = apply_bandpass(adapt_arr, BLINK_BAND)
    except ValueError:
        bl = np.zeros_like(adapt_arr)
    try:
        rl = apply_bandpass(adapt_arr, ROLL_BAND)
    except ValueError:
        rl = np.zeros_like(adapt_arr)
    blink_buf.append(bl[-1] if bl.size else 0)
    roll_buf.append(rl[-1] if rl.size else 0)

    # 8) Heart rate via QRS peaks on notch-filtered ECG
    # apply QRS band-pass to full notch output
    try:
        qrs_arr = apply_bandpass(nt, QRS_BAND)
    except Exception:
        qrs_arr = np.zeros_like(nt)
    # detect rising edges above threshold
    peaks = detect_peaks(qrs_arr, QRS_THRESH)
    # compute RR intervals
    if len(peaks) > 1:
        # convert sample indices to times
        times = peaks / FS
        diffs = np.diff(times)
        rr_intervals.extend(diffs)
        if len(rr_intervals) > 1:
            avg_rr = np.mean(rr_intervals)
            hr = 60.0 / avg_rr if avg_rr > 0 else None
            # valid human range
            if hr and 40 < hr < 180:
                logging.info(f"❤️ HR: {hr:.1f} BPM")

    # update plots
    stage_bufs = [
        list(ecg_lsb_buf),
        list(ecg_mV_buf),
        list(hpf_buf),
        list(notch_buf),
        list(adapt_buf),
        list(blink_buf),
        list(roll_buf)
    ]
    for ln, buf in zip(lines, stage_bufs):
        if not buf: continue
        x = time_axis[-len(buf):]
        ln.set_data(x, buf)
        ln.axes.relim(); ln.axes.autoscale_view()
    plt.draw(); plt.pause(0.001)

# Main async loop

async def main():
    logging.info("Connecting to SensorTile…")
    async with BleakClient(ADDRESS, timeout=30) as client:
        if not client.is_connected:
            logging.error("Connection failed.")
            return
        logging.info("Connected.")

        await client.start_notify(CHAR_NOTIFY, notification_handler)
        await client.start_notify(CHAR_WRITE, notification_handler)
        # start vAFE streaming
        await client.write_gatt_char(CHAR_WRITE, bytearray([0x32, 0x01, 0x0A]), response=False)
        logging.info("Streaming… Ctrl+C to quit.")

        try:
            while True:
                await asyncio.sleep(1)
        except KeyboardInterrupt:
            logging.info("Stopping...")
        finally:
            await client.stop_notify(CHAR_NOTIFY)
            await client.stop_notify(CHAR_WRITE)

if __name__ == '__main__':
    asyncio.run(main())
