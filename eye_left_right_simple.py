#!/usr/bin/env python3
"""
Horizontal EOG Left/Right Detector — Windows-friendly + fail-fast BLE
- Robust left/right only (asymmetric thresholds, sustain + slope + cooldown)
- Bandpass 0.1–15 Hz + 60 Hz notch; median baseline
- 2x2 plots
- Windows Bleak fixes:
    * SelectorEventLoopPolicy
    * Explicit scan timeout (no hang)
    * Clear errors if device not found / not notifying
"""

import asyncio
import struct
import logging
from collections import deque
import time
import threading
import sys
import contextlib

import numpy as np
import matplotlib.pyplot as plt
import matplotlib.animation as animation
from scipy.signal import butter, filtfilt, iirnotch, medfilt, savgol_filter
from bleak import BleakClient, BleakScanner, BleakError

# ====== Your BLE MAC/ADDR ======
try:
    from mac import ADDRESS  # Create mac.py with ADDRESS="AA:BB:CC:DD:EE:FF"
except Exception:
    ADDRESS = None

# ====== Config ======
FS = 240.0
VAFE_GAIN_LSB_PER_MV = 78

# Filtering
EOG_HIGHPASS = 0.1
EOG_LOWPASS  = 15.0
FILTER_ORDER = 4
NOTCH_F = 60.0
NOTCH_Q = 35.0

# Detection (asymmetric)
RIGHT_THRESH_MV = 4.0
LEFT_THRESH_MV  = -1.0
MIN_HOLD_SEC = 0.12
SLOPE_MIN_MV_PER_S = 3.0
WINDOW_VALID_SEC = 0.25
DETECTION_COOLDOWN = 2.0

# Buffers / plotting
PLOT_WINDOW_SEC = 15.0
PLOT_UPDATE_MS = 50
BUFFER_SEC = 8.0
BUFFER_SIZE = int(FS * BUFFER_SEC)
PLOT_BUFFER_SIZE = int(FS * PLOT_WINDOW_SEC)
BASELINE_WINDOW = int(FS * 2.5)
MIN_LENGTH_FOR_FILTER = 50
MIN_LENGTH_FOR_NOTCH = 25

# BLE UUIDs
CHAR_UUID_NOTIFY = "00000001-0004-11e1-ac36-0002a5d5c51b"

# BLE timeouts (Windows sanity)
SCAN_TIMEOUT_S = 8.0          # stop waiting if device not found
CONNECT_TIMEOUT_S = 10.0      # connect phase
NOTIFY_WATCHDOG_S = 6.0       # if we get no samples this long, warn

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)

# ====== Shared state ======
signal_buffer = deque(maxlen=BUFFER_SIZE)
plot_buffer_raw = deque(maxlen=PLOT_BUFFER_SIZE)
plot_buffer_filtered = deque(maxlen=PLOT_BUFFER_SIZE)
plot_buffer_baseline = deque(maxlen=PLOT_BUFFER_SIZE)
plot_buffer_smoothed = deque(maxlen=PLOT_BUFFER_SIZE)
timestamps_plot = deque(maxlen=PLOT_BUFFER_SIZE)
baseline_buffer = deque(maxlen=BASELINE_WINDOW)
data_lock = threading.Lock()

movement_detected_flag = {'detected': False, 'direction': None, 'amplitude': 0.0}

last_detection_time = 0.0
state = "IDLE"       # IDLE, ARMED_RIGHT, ARMED_LEFT, COOLING
cross_time = None
cross_dir = None

# Keep animation
ANI = None

# Watchdog
_last_sample_ts = 0.0

# ====== Filters ======
def butter_bandpass(lowcut, highcut, fs, order=4):
    nyq = 0.5 * fs
    b, a = butter(order, [lowcut/nyq, highcut/nyq], btype='band')
    return b, a

def apply_notch(data, fs, f0=NOTCH_F, Q=NOTCH_Q):
    if len(data) < MIN_LENGTH_FOR_NOTCH:
        return data
    nyq = 0.5 * fs
    w0 = f0 / nyq
    b, a = iirnotch(w0, Q)
    required_length = 3 * max(len(a), len(b))
    if len(data) <= required_length:
        return data
    return filtfilt(b, a, data)

def apply_savgol_smoothing(data, window=21, polyorder=3):
    if len(data) < window or window < polyorder + 2:
        return data
    if window % 2 == 0:
        window += 1
    window = min(window, len(data) - (1 - len(data) % 2))
    if window < polyorder + 2:
        return data
    try:
        return savgol_filter(data, window, polyorder)
    except Exception:
        return data

def apply_movement_filter(data, fs):
    if len(data) < 20:
        return np.asarray(data, dtype=float)
    y = medfilt(np.asarray(data, dtype=float), kernel_size=3)
    try:
        b, a = butter_bandpass(EOG_HIGHPASS, EOG_LOWPASS, fs, order=FILTER_ORDER)
        required_length = 3 * max(len(a), len(b))
        if len(y) <= required_length:
            b, a = butter_bandpass(EOG_HIGHPASS, EOG_LOWPASS, fs, order=2)
            required_length = 3 * max(len(a), len(b))
            if len(y) <= required_length:
                return y
        y = filtfilt(b, a, y)
    except Exception as e:
        logging.warning(f"Bandpass fallback: {e}")
        return y
    y = apply_notch(y, fs)
    return y

def calculate_adaptive_baseline(data, window_size=BASELINE_WINDOW):
    if len(data) < 5:
        return 0.0
    return float(np.median(np.asarray(data, dtype=float)[-window_size:]))

# ====== Detection ======
def detect_left_right(filtered_signal, baseline, ts_array):
    global state, cross_time, cross_dir, last_detection_time, movement_detected_flag

    if len(filtered_signal) < 6:
        return False

    now = ts_array[-1]
    cur = float(filtered_signal[-1] - baseline)

    # Cooldown
    if state == "COOLING":
        if now - last_detection_time >= DETECTION_COOLDOWN:
            state = "IDLE"
        else:
            return False

    # Slope over ~50 ms
    idx_win = int(max(2, min(10, FS * 0.05)))
    slope = (filtered_signal[-1] - filtered_signal[-idx_win]) / (ts_array[-1] - ts_array[-idx_win])

    if state == "IDLE":
        if cur >= RIGHT_THRESH_MV and slope >= SLOPE_MIN_MV_PER_S:
            state = "ARMED_RIGHT"; cross_time = now; cross_dir = "right"
            return False
        if cur <= LEFT_THRESH_MV and -slope >= SLOPE_MIN_MV_PER_S:
            state = "ARMED_LEFT"; cross_time = now; cross_dir = "left"
            return False

    if state in ("ARMED_RIGHT", "ARMED_LEFT"):
        hold = now - (cross_time or now)
        if hold >= MIN_HOLD_SEC:
            win_sec = min(WINDOW_VALID_SEC, hold)
            nwin = int(max(3, FS * win_sec))
            seg = (filtered_signal[-nwin:] - baseline)
            if cross_dir == "right":
                frac = float(np.mean(seg >= RIGHT_THRESH_MV * 0.9))
                if frac >= 0.7:
                    last_detection_time = now
                    state = "COOLING"
                    peak = float(np.max(seg))
                    movement_detected_flag = {'detected': True, 'direction': 'right', 'amplitude': peak}
                    print(f"{time.strftime('%H:%M:%S')} ➡️ LOOK RIGHT  (peak ≈ {peak:.2f} mV)")
                    return True
            else:
                frac = float(np.mean(seg <= LEFT_THRESH_MV * 0.9))
                if frac >= 0.7:
                    last_detection_time = now
                    state = "COOLING"
                    trough = float(np.min(seg))
                    movement_detected_flag = {'detected': True, 'direction': 'left', 'amplitude': trough}
                    print(f"{time.strftime('%H:%M:%S')} ⬅️ LOOK LEFT   (min  ≈ {trough:.2f} mV)")
                    return True
        # Disarm if falls back
        if state == "ARMED_RIGHT" and cur < RIGHT_THRESH_MV * 0.6:
            state = "IDLE"; cross_time = None
        if state == "ARMED_LEFT" and cur > LEFT_THRESH_MV * 0.6:
            state = "IDLE"; cross_time = None

    return False

# ====== Plotting ======
class HorizontalMovementPlotter:
    def __init__(self):
        plt.style.use('default')
        self.fig, ((self.ax1, self.ax2), (self.ax3, self.ax4)) = plt.subplots(2, 2, figsize=(18, 12))
        self.fig.suptitle('Horizontal EOG: ⬅️ Left | ➡️ Right', fontsize=16)

        (self.line_raw,) = self.ax1.plot([], [], label='Raw', linewidth=1)
        (self.line_filtered,) = self.ax1.plot([], [], label='Filtered', linewidth=2)
        (self.line_smoothed,) = self.ax1.plot([], [], label='Smoothed', linewidth=1.5)
        self.ax1.set_ylabel('mV'); self.ax1.set_title('Signal Processing')
        self.ax1.legend(); self.ax1.grid(True, alpha=0.3)

        (self.line_signal,) = self.ax2.plot([], [], label='Filtered', linewidth=2)
        (self.line_baseline,) = self.ax2.plot([], [], label='Baseline', linewidth=2)
        self.ax2.set_ylabel('mV'); self.ax2.set_title('Adaptive Baseline')
        self.ax2.legend(); self.ax2.grid(True, alpha=0.3)

        (self.line_corrected,) = self.ax3.plot([], [], label='Corrected', linewidth=2)
        (self.line_thresh_high_pos,) = self.ax3.plot([], [], label=f'Right ≥ {RIGHT_THRESH_MV} mV', linewidth=1)
        (self.line_thresh_high_neg,) = self.ax3.plot([], [], linewidth=1)
        self.ax3.set_ylabel('mV'); self.ax3.set_xlabel('Time (s)')
        self.ax3.set_title('Left/Right Detection')
        self.ax3.legend(); self.ax3.grid(True, alpha=0.3)

        self.ax4.clear(); self.ax4.set_xlim(0, 10); self.ax4.set_ylim(0, 6)
        self.ax4.set_title('Status'); self.ax4.grid(True, alpha=0.3)
        self.command_text = self.ax4.text(5, 4.5, 'Ready...', ha='center', va='center', fontsize=12,
                                          bbox=dict(boxstyle="round,pad=0.3"))
        self.movement_text = self.ax4.text(5, 3, 'Last: None', ha='center', va='center', fontsize=10)
        self.movement_markers = []

    def update_plot(self, _frame):
        global movement_detected_flag

        with data_lock:
            if len(timestamps_plot) < 10:
                return tuple()

            times = np.array(list(timestamps_plot))
            raw_data = np.array(list(plot_buffer_raw))
            filtered_data = np.array(list(plot_buffer_filtered))
            smoothed_data = np.array(list(plot_buffer_smoothed))
            baseline_data = np.array(list(plot_buffer_baseline))

            if movement_detected_flag['detected']:
                self.mark_movement(movement_detected_flag['direction'])
                self.movement_text.set_text(
                    f"Last: {movement_detected_flag['direction'].upper()} ({movement_detected_flag['amplitude']:.1f} mV)"
                )
                movement_detected_flag = {'detected': False, 'direction': None, 'amplitude': 0.0}

        if len(times) > 0:
            times = times - times[-1]

        self.line_raw.set_data(times, raw_data)
        self.line_filtered.set_data(times, filtered_data)
        self.line_smoothed.set_data(times, smoothed_data)

        self.line_signal.set_data(times, filtered_data)
        self.line_baseline.set_data(times, baseline_data)

        corrected = filtered_data - baseline_data
        self.line_corrected.set_data(times, corrected)

        if len(times) > 0:
            th_pos = np.full_like(times, RIGHT_THRESH_MV, dtype=float)
            th_neg = np.full_like(times, LEFT_THRESH_MV, dtype=float)
            self.line_thresh_high_pos.set_data(times, th_pos)
            self.line_thresh_high_neg.set_data(times, th_neg)

        for ax in (self.ax1, self.ax2, self.ax3):
            ax.relim(); ax.autoscale_view()
            if len(times) > 0:
                ax.set_xlim(-PLOT_WINDOW_SEC, 0)

        return tuple()

    def mark_movement(self, direction):
        current_time = 0
        for ax in (self.ax1, self.ax2, self.ax3):
            marker = ax.axvline(current_time, linestyle='-', linewidth=3, alpha=0.8)
            self.movement_markers.append(marker)
        if len(self.movement_markers) > 40:
            for m in self.movement_markers[:20]:
                with contextlib.suppress(Exception):
                    m.remove()
            self.movement_markers = self.movement_markers[20:]

        text = '➡️ RIGHT' if direction == 'right' else '⬅️ LEFT'
        self.command_text.set_text(text)
        threading.Timer(3.0, self.clear_command_display).start()

    def clear_command_display(self):
        self.command_text.set_text('Ready...')

# ====== BLE Helpers ======
async def resolve_device(address: str, timeout: float):
    """Find the device by address with a firm timeout to avoid hangs."""
    logging.info(f"Scanning up to {timeout:.0f}s for device {address} …")
    device = await BleakScanner.find_device_by_address(address, timeout=timeout)
    if not device:
        raise BleakError(f"Device {address} not found after {timeout}s. "
                         f"Ensure Bluetooth is on, device is advertising, and address is correct.")
    logging.info("Device found.")
    return device

async def run_ble(address):
    """Scan → connect → subscribe. Warn if no data arrives."""
    global _last_sample_ts

    dev = await resolve_device(address, SCAN_TIMEOUT_S)

    # Connect (Bleak doesn't have a built-in timeout here; we implement a wrapper)
    client = BleakClient(dev)
    try:
        logging.info("Connecting …")
        async def _connect():
            await client.connect()

        await asyncio.wait_for(_connect(), timeout=CONNECT_TIMEOUT_S)
        if not await client.is_connected():
            raise BleakError("Connected=False after connect()")
        logging.info("Connected. Subscribing to notifications …")

        await client.start_notify(CHAR_UUID_NOTIFY, notification_handler)
        _last_sample_ts = time.time()

        # Watchdog loop while plot window open
        while plt.fignum_exists(plt.gcf().number):
            await asyncio.sleep(0.5)
            # If no samples for a while, warn (don’t disconnect automatically — just inform)
            if time.time() - _last_sample_ts > NOTIFY_WATCHDOG_S:
                logging.warning("No EOG samples received for a few seconds. "
                                "Check SensorTile stream settings / UUID / distance.")
                _last_sample_ts = time.time()  # avoid spamming

    except asyncio.TimeoutError:
        raise BleakError(f"Connect timeout (> {CONNECT_TIMEOUT_S}s). Device busy or out of range?")
    finally:
        with contextlib.suppress(Exception):
            await client.stop_notify(CHAR_UUID_NOTIFY)
        with contextlib.suppress(Exception):
            await client.disconnect()
        logging.info("BLE disconnected.")

# ====== Notification ======
def notification_handler(_sender, data: bytearray):
    """Parse EOG sample, filter, baseline-correct, detect, and store for plots."""
    global _last_sample_ts
    try:
        if len(data) < 65:
            return
        eog_raw_lsb, = struct.unpack('<f', data[61:65])
        eog_raw_mv = eog_raw_lsb / VAFE_GAIN_LSB_PER_MV
        current_time = time.time()
        _last_sample_ts = current_time

        with data_lock:
            signal_buffer.append(eog_raw_mv)
            plot_buffer_raw.append(eog_raw_mv)
            timestamps_plot.append(current_time)
            baseline_buffer.append(eog_raw_mv)

            if len(signal_buffer) < MIN_LENGTH_FOR_FILTER:
                plot_buffer_filtered.append(eog_raw_mv)
                plot_buffer_smoothed.append(eog_raw_mv)
                plot_buffer_baseline.append(0.0)
                return

            filtered = apply_movement_filter(list(signal_buffer), FS)
            baseline = calculate_adaptive_baseline(list(baseline_buffer))
            smoothed = apply_savgol_smoothing(filtered)

            plot_buffer_filtered.append(filtered[-1])
            plot_buffer_smoothed.append(smoothed[-1])
            plot_buffer_baseline.append(baseline)

            ts_array = np.array(list(timestamps_plot), dtype=float)
            detect_left_right(filtered, baseline, ts_array)

    except Exception as e:
        logging.error(f"notification_handler error: {e}")

# ====== Main ======
async def main():
    if not ADDRESS:
        logging.error("BLE ADDRESS not set. Create mac.py with ADDRESS='AA:BB:CC:DD:EE:FF'.")
        return

    plotter = HorizontalMovementPlotter()

    global ANI
    ANI = animation.FuncAnimation(
        plotter.fig,
        plotter.update_plot,
        interval=PLOT_UPDATE_MS,
        blit=False,
        cache_frame_data=False
    )

    # Start BLE task with fail-fast scan/connect
    ble_task = asyncio.create_task(run_ble(ADDRESS))

    try:
        plt.show()
    finally:
        if not ble_task.done():
            ble_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await ble_task

# Windows policy so Bleak + GUI coexist
if __name__ == "__main__":
    if sys.platform.startswith("win"):
        with contextlib.suppress(Exception):
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    try:
        asyncio.run(main())
    except BleakError as e:
        logging.error(e)
    except KeyboardInterrupt:
        print("Exiting…")
