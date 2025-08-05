#!/usr/bin/env python3
"""
EOG Visual Filter with Hard Blink Detection
- Real-time plotting to visualize signal
- Proper baseline correction
- High thresholds for hard blinks only
- Better noise filtering
- Fixed matplotlib threading issues
"""

import asyncio
import struct
import logging
from collections import deque
import time
import threading
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import matplotlib.pyplot as plt
import matplotlib.animation as animation
from scipy.signal import butter, filtfilt, iirnotch, medfilt
from bleak import BleakClient
from mac import ADDRESS  # your SensorTile BLE address

# ——— VISUAL CONFIG ——————————————————————————————————————————————
PLOT_WINDOW_SEC = 10.0        # Show last 10 seconds of data
PLOT_UPDATE_MS = 100          # Update plot every 100ms

# ——— SIGNAL CONFIG ——————————————————————————————————————————————
FS = 240.0                    # sampling rate (Hz)
VAFE_GAIN_LSB_PER_MV = 78     # From datasheet: 78 LSB/mV

# Filtering parameters
NOTCH_F = 60.0               # mains notch freq (Hz)
NOTCH_Q = 30.0               # notch Q
EOG_LOWPASS = 40.0           # Increased to preserve pulse (was 15Hz)
EOG_HIGHPASS = 0.1           # Lower to preserve slow pulse components (was 0.5Hz)
FILTER_ORDER = 4             # Reduced from 6th order for gentler filtering

# Buffer sizes
BUFFER_SEC = 0.5             # Processing buffer (500ms)
BUFFER_SIZE = int(FS * BUFFER_SEC)
PLOT_BUFFER_SIZE = int(FS * PLOT_WINDOW_SEC)

# MUCH HIGHER THRESHOLDS for hard blinks only
HARD_BLINK_THRESH = 8.0      # 8mV threshold (much higher!)
BLINK_MIN_SAMPLES = int(FS * 0.05)   # 50ms minimum duration
BLINK_MAX_SAMPLES = int(FS * 0.5)    # 500ms maximum duration
BASELINE_WINDOW = int(FS * 2.0)      # 2 second baseline calculation

# Detection cooldown
DETECTION_COOLDOWN = 1.0     # 1 second between detections

# Minimum signal lengths for filtering
MIN_LENGTH_FOR_FILTER = 60   # Conservative minimum for 4th order filter
MIN_LENGTH_FOR_NOTCH = 30    # Minimum for notch filter

# Filtering mode: 'minimal', 'standard', or 'aggressive'
FILTER_MODE = 'minimal'      # Try 'minimal' first to preserve pulse

# BLE UUIDs
CHAR_UUID_NOTIFY = "00000001-0004-11e1-ac36-0002a5d5c51b"
CHAR_UUID_WRITE = "00000002-0004-11e1-ac36-0002a5d5c51b"

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)

# Data buffers (thread-safe)
signal_buffer = deque(maxlen=BUFFER_SIZE)
plot_buffer_raw = deque(maxlen=PLOT_BUFFER_SIZE)
plot_buffer_filtered = deque(maxlen=PLOT_BUFFER_SIZE)
plot_buffer_baseline = deque(maxlen=PLOT_BUFFER_SIZE)
timestamps_plot = deque(maxlen=PLOT_BUFFER_SIZE)

# Detection state
last_detection_time = 0
baseline_buffer = deque(maxlen=BASELINE_WINDOW)

# Threading control
data_lock = threading.Lock()
ble_client = None
ble_connected = False
blink_detected_flag = False

# ——— ENHANCED FILTERING ———————————————————————————————————————

def butter_bandpass(lowcut, highcut, fs, order=4):
    """Bandpass filter"""
    nyq = 0.5 * fs
    low = lowcut / nyq
    high = highcut / nyq
    b, a = butter(order, [low, high], btype='band')
    return b, a

def apply_notch(data, fs, f0=NOTCH_F, Q=NOTCH_Q):
    """Notch filter for 60Hz mains with length validation"""
    if len(data) < MIN_LENGTH_FOR_NOTCH:
        return data
        
    nyq = fs / 2
    if f0 >= nyq:
        f_mod = f0 % fs
        f0_use = fs - f_mod if f_mod > nyq else f_mod
    else:
        f0_use = f0
    
    w0 = f0_use / nyq
    
    try:
        b, a = iirnotch(w0, Q)
        
        # Check if signal is long enough for filtfilt with notch filter
        required_length = 3 * max(len(a), len(b))
        if len(data) <= required_length:
            return data
            
        return filtfilt(b, a, data)
    except ValueError as e:
        if "padlen" in str(e):
            logging.warning(f"Notch filter failed due to short signal length ({len(data)} samples), skipping notch")
            return data
        else:
            raise e

def apply_eog_filter(data, fs):
    """Complete EOG filtering pipeline optimized for pulse preservation"""
    if len(data) < 20:
        return data
    
    # Different filtering strategies
    if FILTER_MODE == 'minimal':
        # Minimal filtering - preserve almost everything
        if len(data) < 10:
            return data
        # Only remove 60Hz noise and extreme spikes
        data_clean = medfilt(data, kernel_size=3)
        if len(data_clean) >= MIN_LENGTH_FOR_NOTCH:
            data_clean = apply_notch(data_clean, fs)
        return data_clean
        
    elif FILTER_MODE == 'standard':
        # Standard filtering for general use
        data_median = medfilt(data, kernel_size=3)
        
        if len(data_median) < MIN_LENGTH_FOR_FILTER:
            return data_median
        
        try:
            # Bandpass filter (0.1-40 Hz to preserve pulse)
            b, a = butter_bandpass(EOG_HIGHPASS, EOG_LOWPASS, fs, order=FILTER_ORDER)
            
            required_length = 3 * max(len(a), len(b))
            if len(data_median) <= required_length:
                b, a = butter_bandpass(EOG_HIGHPASS, EOG_LOWPASS, fs, order=2)
                required_length = 3 * max(len(a), len(b))
                
                if len(data_median) <= required_length:
                    return data_median
            
            data_bandpass = filtfilt(b, a, data_median)
            data_notched = apply_notch(data_bandpass, fs)
            return data_notched
            
        except ValueError as e:
            if "padlen" in str(e):
                logging.warning(f"Standard filter failed, using minimal filtering")
                return medfilt(data, kernel_size=3)
            else:
                raise e
                
    else:  # aggressive
        # Aggressive filtering (original settings)
        data_median = medfilt(data, kernel_size=min(3, len(data)))
        
        if len(data_median) < MIN_LENGTH_FOR_FILTER:
            return data_median
        
        try:
            b, a = butter_bandpass(0.5, 15.0, fs, order=6)  # Original aggressive settings
            
            required_length = 3 * max(len(a), len(b))
            if len(data_median) <= required_length:
                b, a = butter_bandpass(0.5, 15.0, fs, order=2)
                required_length = 3 * max(len(a), len(b))
                
                if len(data_median) <= required_length:
                    return data_median
            
            data_bandpass = filtfilt(b, a, data_median)
            data_notched = apply_notch(data_bandpass, fs)
            return data_notched
            
        except ValueError as e:
            if "padlen" in str(e):
                return data_median
            else:
                raise e

def calculate_baseline(data):
    """Calculate rolling baseline (median of recent data)"""
    if len(data) < 10:
        return 0.0
    return np.median(data[-BASELINE_WINDOW//4:])

def detect_hard_blink(filtered_signal, baseline, fs):
    """Detect only HARD blinks with strict criteria"""
    global last_detection_time, blink_detected_flag
    
    current_time = time.time()
    
    # Cooldown check
    if current_time - last_detection_time < DETECTION_COOLDOWN:
        return False
    
    if len(filtered_signal) < BLINK_MIN_SAMPLES:
        return False
    
    # Baseline-corrected signal
    corrected_signal = filtered_signal - baseline
    current_amplitude = abs(corrected_signal[-1])
    
    # Check if current amplitude exceeds hard blink threshold
    if current_amplitude > HARD_BLINK_THRESH:
        # Additional validation: check sustained amplitude
        recent_samples = corrected_signal[-BLINK_MIN_SAMPLES:]
        high_amplitude_count = np.sum(np.abs(recent_samples) > HARD_BLINK_THRESH * 0.8)
        
        # Require at least 60% of recent samples to be high amplitude
        if high_amplitude_count > BLINK_MIN_SAMPLES * 0.6:
            last_detection_time = current_time
            blink_detected_flag = True
            return True
    
    return False

# ——— PLOTTING SETUP ———————————————————————————————————————————

class EOGPlotter:
    def __init__(self):
        plt.style.use('default')
        self.fig, (self.ax1, self.ax2) = plt.subplots(2, 1, figsize=(12, 8))
        self.fig.suptitle('EOG Signal Monitor - Hard Blink Detection', fontsize=14)
        
        # Plot 1: Raw vs Filtered
        self.line_raw, = self.ax1.plot([], [], 'b-', alpha=0.7, label='Raw Signal', linewidth=1)
        self.line_filtered, = self.ax1.plot([], [], 'g-', label='Filtered Signal', linewidth=2)
        self.line_baseline, = self.ax1.plot([], [], 'r--', label='Baseline', linewidth=1)
        self.ax1.set_ylabel('Amplitude (mV)')
        self.ax1.set_title('EOG Signal Processing')
        self.ax1.legend()
        self.ax1.grid(True, alpha=0.3)
        
        # Plot 2: Baseline-corrected with threshold
        self.line_corrected, = self.ax2.plot([], [], 'purple', label='Baseline Corrected', linewidth=2)
        self.line_thresh_pos, = self.ax2.plot([], [], 'r--', label=f'Threshold (±{HARD_BLINK_THRESH}mV)', linewidth=1)
        self.line_thresh_neg, = self.ax2.plot([], [], 'r--', linewidth=1)
        self.ax2.set_ylabel('Amplitude (mV)')
        self.ax2.set_xlabel('Time (seconds)')
        self.ax2.set_title('Hard Blink Detection')
        self.ax2.legend()
        self.ax2.grid(True, alpha=0.3)
        
        # Detection markers
        self.blink_markers = []
        
    def update_plot(self, frame):
        global blink_detected_flag
        
        with data_lock:
            if len(timestamps_plot) < 10:
                return self.line_raw, self.line_filtered, self.line_baseline, self.line_corrected
            
            # Convert timestamps to relative seconds
            times = np.array(list(timestamps_plot))
            raw_data = np.array(list(plot_buffer_raw))
            filtered_data = np.array(list(plot_buffer_filtered))
            baseline_data = np.array(list(plot_buffer_baseline))
            
            # Check for blink detection
            if blink_detected_flag:
                self.mark_blink()
                blink_detected_flag = False
        
        if len(times) > 0:
            times = times - times[-1]  # Relative to current time
        
        # Update raw, filtered, and baseline plots
        self.line_raw.set_data(times, raw_data)
        self.line_filtered.set_data(times, filtered_data)
        self.line_baseline.set_data(times, baseline_data)
        
        # Update baseline-corrected plot
        corrected_data = filtered_data - baseline_data
        self.line_corrected.set_data(times, corrected_data)
        
        # Update threshold lines
        if len(times) > 0:
            thresh_pos = np.full_like(times, HARD_BLINK_THRESH)
            thresh_neg = np.full_like(times, -HARD_BLINK_THRESH)
            self.line_thresh_pos.set_data(times, thresh_pos)
            self.line_thresh_neg.set_data(times, thresh_neg)
        
        # Auto-scale axes
        for ax in [self.ax1, self.ax2]:
            ax.relim()
            ax.autoscale_view()
            if len(times) > 0:
                ax.set_xlim(-PLOT_WINDOW_SEC, 0)
        
        return self.line_raw, self.line_filtered, self.line_baseline, self.line_corrected
    
    def mark_blink(self):
        """Add a blink detection marker"""
        current_time = 0  # Will be at the right edge
        for ax in [self.ax1, self.ax2]:
            marker = ax.axvline(current_time, color='red', linestyle='-', linewidth=3, alpha=0.8)
            self.blink_markers.append(marker)
        
        # Remove old markers (keep only last 5)
        if len(self.blink_markers) > 10:
            for marker in self.blink_markers[:5]:
                marker.remove()
            self.blink_markers = self.blink_markers[5:]

# ——— NOTIFICATION HANDLER ———————————————————————————————————————

def notification_handler(sender, data: bytearray):
    """Enhanced notification handler with robust error handling"""
    try:
        # Ensure full packet
        if len(data) < 65:
            return
        
        # Unpack and convert to mV
        eog_raw_lsb, = struct.unpack('<f', data[61:65])
        eog_raw_mv = eog_raw_lsb / VAFE_GAIN_LSB_PER_MV
        
        current_time = time.time()
        
        with data_lock:
            # Add to buffers
            signal_buffer.append(eog_raw_mv)
            plot_buffer_raw.append(eog_raw_mv)
            timestamps_plot.append(current_time)
            baseline_buffer.append(eog_raw_mv)
            
            # Only process when we have enough data for meaningful filtering
            if len(signal_buffer) < MIN_LENGTH_FOR_FILTER:
                plot_buffer_filtered.append(eog_raw_mv)  # Use raw until filtered available
                plot_buffer_baseline.append(0.0)
                return
            
            # Apply filtering with robust error handling
            signal_array = np.array(list(signal_buffer))
            filtered_signal = apply_eog_filter(signal_array, FS)
            
            # Calculate baseline
            baseline = calculate_baseline(list(baseline_buffer))
            
            # Store for plotting
            plot_buffer_filtered.append(filtered_signal[-1])
            plot_buffer_baseline.append(baseline)
            
            # Detection (every 10th sample to reduce computation)
            if len(signal_buffer) % 10 == 0:
                # Hard blink detection
                blink_detected = detect_hard_blink(filtered_signal, baseline, FS)
                
                # Logging (reduced frequency)
                baseline_corrected = abs(filtered_signal[-1] - baseline)
                logging.info(
                    f"EOG: raw={eog_raw_mv:.2f}mV | filtered={filtered_signal[-1]:.2f}mV | "
                    f"baseline={baseline:.2f}mV | corrected={baseline_corrected:.2f}mV"
                )
                
                if blink_detected:
                    logging.info(f"🔥 HARD BLINK DETECTED! Amplitude: {baseline_corrected:.2f}mV")
                    
    except Exception as e:
        logging.error(f"Error in notification handler: {e}")
        # Fallback: use raw signal for plotting
        if 'eog_raw_mv' in locals():
            with data_lock:
                plot_buffer_filtered.append(eog_raw_mv)
                plot_buffer_baseline.append(0.0)

# ——— BLE CONNECTION FUNCTIONS ———————————————————————————————————

async def ble_connection():
    """Handle BLE connection in background"""
    global ble_client, ble_connected
    
    try:
        ble_client = BleakClient(ADDRESS, timeout=30.0)
        await ble_client.connect()
        
        if not ble_client.is_connected:
            logging.error("❌ Failed to connect to SensorTile.")
            return
            
        ble_connected = True
        logging.info("✅ Connected to SensorTile.")

        # Subscribe to notifications
        await ble_client.start_notify(CHAR_UUID_NOTIFY, notification_handler)
        await ble_client.start_notify(CHAR_UUID_WRITE, notification_handler)
        
        # Start vAFE stream
        await ble_client.write_gatt_char(CHAR_UUID_WRITE, bytearray([0x32,0x01,0x0A]), response=False)
        logging.info("📡 Streaming EOG with visual monitoring.")

        # Keep connection alive
        while ble_connected:
            await asyncio.sleep(1)
            
    except Exception as e:
        logging.error(f"BLE connection error: {e}")
    finally:
        if ble_client and ble_client.is_connected:
            await ble_client.stop_notify(CHAR_UUID_NOTIFY)
            await ble_client.stop_notify(CHAR_UUID_WRITE)
            await ble_client.disconnect()
            logging.info("✅ Disconnected from SensorTile.")

def run_ble_async():
    """Run BLE connection in separate thread"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(ble_connection())

# ——— MAIN FUNCTION ——————————————————————————————————————————————

def main():
    """Main function - runs matplotlib on main thread"""
    logging.info("🚀 Starting EOG Visual Monitor with Hard Blink Detection")
    logging.info(f"📊 Sampling Rate: {FS} Hz")
    logging.info(f"🔧 Bandpass Filter: {EOG_HIGHPASS}-{EOG_LOWPASS} Hz")
    logging.info(f"⚡ Notch Filter: {NOTCH_F} Hz")
    logging.info(f"👁️  Hard Blink Threshold: {HARD_BLINK_THRESH} mV")
    logging.info(f"⏰ Detection Cooldown: {DETECTION_COOLDOWN} seconds")
    logging.info(f"📏 Minimum Filter Length: {MIN_LENGTH_FOR_FILTER} samples")
    
    # Start BLE connection in background thread
    ble_thread = threading.Thread(target=run_ble_async, daemon=True)
    ble_thread.start()
    
    # Give BLE time to connect
    time.sleep(3)
    
    # Set up plotting on main thread
    plotter = EOGPlotter()
    
    def on_close(event):
        global ble_connected
        ble_connected = False
        logging.info("🛑 Plot window closed, stopping BLE connection...")
    
    plotter.fig.canvas.mpl_connect('close_event', on_close)
    
    # Start animation
    ani = animation.FuncAnimation(
        plotter.fig, 
        plotter.update_plot, 
        interval=PLOT_UPDATE_MS, 
        blit=False,
        cache_frame_data=False
    )
    
    plt.tight_layout()
    logging.info("📊 Starting plot - Close window to stop.")
    plt.show()
    
    # Clean up
    ble_connected = False
    logging.info("✅ Application closed.")

if __name__ == '__main__':
    main()