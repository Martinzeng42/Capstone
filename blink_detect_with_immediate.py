#!/usr/bin/env python3
"""
EOG Visual Filter with Improved Blink Detection
- Reduced noise through better filtering
- Lower threshold for moderate blinks (not just hard blinks)
- Adaptive baseline tracking
- Enhanced signal conditioning
- Better false positive rejection
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
from scipy.signal import butter, filtfilt, iirnotch, medfilt, savgol_filter
from bleak import BleakClient
from mac import ADDRESS  # your SensorTile BLE address

# ——— VISUAL CONFIG ——————————————————————————————————————————————
PLOT_WINDOW_SEC = 10.0        # Show last 10 seconds of data
PLOT_UPDATE_MS = 100          # Update plot every 100ms

# ——— SIGNAL CONFIG ——————————————————————————————————————————————
FS = 240.0                    # sampling rate (Hz)
VAFE_GAIN_LSB_PER_MV = 78     # From datasheet: 78 LSB/mV

# Enhanced filtering parameters for noise reduction
NOTCH_F = 60.0               # mains notch freq (Hz)
NOTCH_Q = 35.0               # Higher Q for sharper notch
EOG_LOWPASS = 35.0           # Slightly reduced to filter more high-freq noise
EOG_HIGHPASS = 0.05          # Even lower to preserve slow components
FILTER_ORDER = 4             # Keep reasonable order

# Buffer sizes
BUFFER_SEC = 1.0             # Longer processing buffer for better filtering
BUFFER_SIZE = int(FS * BUFFER_SEC)
PLOT_BUFFER_SIZE = int(FS * PLOT_WINDOW_SEC)

# LOWERED THRESHOLDS for moderate blinks
BLINK_THRESH = 3.5           # Reduced from 8.0mV to 3.5mV
BLINK_MIN_SAMPLES = int(FS * 0.04)    # 40ms minimum duration (slightly shorter)
BLINK_MAX_SAMPLES = int(FS * 0.8)     # 800ms maximum duration (longer)
BASELINE_WINDOW = int(FS * 3.0)       # 3 second baseline (longer for stability)

# Enhanced detection parameters
DETECTION_COOLDOWN = 0.5     # Reduced cooldown for more responsive detection
ADAPTIVE_THRESH_FACTOR = 2.0 # Adaptive threshold based on recent signal variance
SAVGOL_WINDOW = 15           # Savitzky-Golay smoothing window
SAVGOL_POLYORDER = 3         # Polynomial order for smoothing

# Minimum signal lengths for filtering
MIN_LENGTH_FOR_FILTER = 50   # Reduced from 60
MIN_LENGTH_FOR_NOTCH = 25    # Reduced from 30

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
plot_buffer_smoothed = deque(maxlen=PLOT_BUFFER_SIZE)
timestamps_plot = deque(maxlen=PLOT_BUFFER_SIZE)

# Enhanced detection state
last_detection_time = 0
baseline_buffer = deque(maxlen=BASELINE_WINDOW)
variance_buffer = deque(maxlen=int(FS * 5.0))  # 5 second variance calculation
blink_state = {'in_blink': False, 'blink_start': 0, 'blink_samples': 0}

# Threading control
data_lock = threading.Lock()
ble_client = None
ble_connected = False
blink_detected_flag = False

# ——— ENHANCED FILTERING WITH NOISE REDUCTION ———————————————————

def butter_bandpass(lowcut, highcut, fs, order=4):
    """Bandpass filter"""
    nyq = 0.5 * fs
    low = lowcut / nyq
    high = highcut / nyq
    b, a = butter(order, [low, high], btype='band')
    return b, a

def apply_notch(data, fs, f0=NOTCH_F, Q=NOTCH_Q):
    """Enhanced notch filter for 60Hz mains with length validation"""
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

def apply_savgol_smoothing(data, window=SAVGOL_WINDOW, polyorder=SAVGOL_POLYORDER):
    """Apply Savitzky-Golay smoothing for noise reduction while preserving features"""
    if len(data) < window:
        return data
    
    # Ensure window is odd
    if window % 2 == 0:
        window += 1
    
    # Ensure window is not larger than data
    window = min(window, len(data))
    if window < polyorder + 1:
        return data
        
    try:
        return savgol_filter(data, window, polyorder)
    except:
        return data

def apply_enhanced_eog_filter(data, fs):
    """Enhanced EOG filtering pipeline with multiple stages for noise reduction"""
    if len(data) < 20:
        return data
    
    # Step 1: Median filter to remove impulse noise (keep minimal)
    data_median = medfilt(data, kernel_size=3)
    
    # Check if we have enough data for bandpass filtering
    if len(data_median) < MIN_LENGTH_FOR_FILTER:
        return data_median
    
    try:
        # Step 2: Bandpass filter with tighter range
        b, a = butter_bandpass(EOG_HIGHPASS, EOG_LOWPASS, fs, order=FILTER_ORDER)
        
        # Check if signal is long enough for filtfilt
        required_length = 3 * max(len(a), len(b))
        if len(data_median) <= required_length:
            # Use lower order filter for shorter signals
            b, a = butter_bandpass(EOG_HIGHPASS, EOG_LOWPASS, fs, order=2)
            required_length = 3 * max(len(a), len(b))
            
            if len(data_median) <= required_length:
                return data_median
        
        data_bandpass = filtfilt(b, a, data_median)
        
        # Step 3: 60Hz notch filter
        data_notched = apply_notch(data_bandpass, fs)
        
        # Step 4: Savitzky-Golay smoothing for additional noise reduction
        data_smoothed = apply_savgol_smoothing(data_notched)
        
        return data_smoothed
        
    except ValueError as e:
        if "padlen" in str(e):
            logging.warning(f"Filter failed due to short signal length ({len(data)} samples), using median filter only")
            return data_median
        else:
            logging.error(f"Unexpected error in filtering: {e}")
            return data_median

def calculate_adaptive_baseline(data):
    """Calculate adaptive baseline using robust statistics"""
    if len(data) < 20:
        return 0.0
    
    # Use median for robust baseline estimation
    recent_data = np.array(data[-BASELINE_WINDOW//2:])
    
    # Remove outliers using IQR method before calculating baseline
    q75, q25 = np.percentile(recent_data, [75, 25])
    iqr = q75 - q25
    lower_bound = q25 - 1.5 * iqr
    upper_bound = q75 + 1.5 * iqr
    
    filtered_data = recent_data[(recent_data >= lower_bound) & (recent_data <= upper_bound)]
    
    if len(filtered_data) > 5:
        return np.median(filtered_data)
    else:
        return np.median(recent_data)

def calculate_signal_variance(data):
    """Calculate recent signal variance for adaptive thresholding"""
    if len(data) < 50:
        return 1.0
    
    recent_data = np.array(data[-int(FS * 2.0):])  # Last 2 seconds
    return np.std(recent_data)

def detect_moderate_blink(filtered_signal, baseline, signal_variance, fs):
    """Simplified blink detection with debugging"""
    global last_detection_time, blink_detected_flag, blink_state
    
    current_time = time.time()
    
    # Cooldown check
    if current_time - last_detection_time < DETECTION_COOLDOWN:
        return False
    
    if len(filtered_signal) < BLINK_MIN_SAMPLES:
        return False
    
    # Baseline-corrected signal
    corrected_signal = filtered_signal - baseline
    current_amplitude = abs(corrected_signal[-1])
    
    # Adaptive threshold based on signal variance
    adaptive_thresh = max(BLINK_THRESH, signal_variance * ADAPTIVE_THRESH_FACTOR)
    adaptive_thresh = min(adaptive_thresh, BLINK_THRESH * 2.0)  # Cap the adaptive threshold
    
    # Debug logging every 50 samples to avoid spam
    if len(filtered_signal) % 50 == 0:
        logging.info(f"DEBUG: current_amp={current_amplitude:.2f}, thresh={adaptive_thresh:.2f}, in_blink={blink_state['in_blink']}")
    
    # Simplified state machine for blink detection
    if not blink_state['in_blink']:
        # Looking for blink onset
        if current_amplitude > adaptive_thresh:
            logging.info(f"🔍 BLINK START: amplitude={current_amplitude:.2f}mV > threshold={adaptive_thresh:.2f}mV")
            blink_state['in_blink'] = True
            blink_state['blink_start'] = len(corrected_signal) - 1
            blink_state['blink_samples'] = 1
            
            # IMMEDIATE DETECTION - trigger on threshold crossing
            last_detection_time = current_time
            blink_detected_flag = True
            blink_state['in_blink'] = False  # Reset immediately for next detection
            logging.info(f"✅ IMMEDIATE BLINK DETECTED! Amplitude: {current_amplitude:.2f}mV")
            return True
            
    else:
        # In potential blink, check continuation or end
        blink_state['blink_samples'] += 1
        
        # Check if still above threshold (with hysteresis)
        if current_amplitude < adaptive_thresh * 0.7:  # 30% hysteresis
            logging.info(f"🔍 BLINK END: duration={blink_state['blink_samples']} samples")
            
            # Validate duration
            if BLINK_MIN_SAMPLES <= blink_state['blink_samples'] <= BLINK_MAX_SAMPLES:
                # Get the blink segment for validation
                start_idx = max(0, blink_state['blink_start'])
                end_idx = min(len(corrected_signal), blink_state['blink_start'] + blink_state['blink_samples'])
                
                if end_idx > start_idx:
                    blink_segment = corrected_signal[start_idx:end_idx]
                    max_amplitude = np.max(np.abs(blink_segment))
                    
                    logging.info(f"🔍 VALIDATING: max_amp={max_amplitude:.2f}, thresh={adaptive_thresh:.2f}")
                    
                    # Relaxed validation - just check peak amplitude
                    if max_amplitude > adaptive_thresh:
                        last_detection_time = current_time
                        blink_detected_flag = True
                        blink_state['in_blink'] = False
                        logging.info(f"✅ VALIDATED BLINK! Max amplitude: {max_amplitude:.2f}mV, Duration: {blink_state['blink_samples']} samples")
                        return True
                    else:
                        logging.info(f"❌ REJECTED: max_amplitude {max_amplitude:.2f} <= threshold {adaptive_thresh:.2f}")
                else:
                    logging.info(f"❌ REJECTED: invalid segment indices {start_idx}:{end_idx}")
            else:
                logging.info(f"❌ REJECTED: duration {blink_state['blink_samples']} not in range [{BLINK_MIN_SAMPLES}, {BLINK_MAX_SAMPLES}]")
            
            # Reset blink state
            blink_state['in_blink'] = False
        
        # Safety check for overly long blinks
        elif blink_state['blink_samples'] > BLINK_MAX_SAMPLES:
            logging.info(f"⚠️  TIMEOUT: blink too long ({blink_state['blink_samples']} samples), resetting")
            blink_state['in_blink'] = False
    
    return False

# ——— ENHANCED PLOTTING SETUP ———————————————————————————————————

class EnhancedEOGPlotter:
    def __init__(self):
        plt.style.use('default')
        self.fig, (self.ax1, self.ax2, self.ax3) = plt.subplots(3, 1, figsize=(14, 10))
        self.fig.suptitle('Enhanced EOG Signal Monitor - Moderate Blink Detection', fontsize=14)
        
        # Plot 1: Raw vs Filtered
        self.line_raw, = self.ax1.plot([], [], 'b-', alpha=0.7, label='Raw Signal', linewidth=1)
        self.line_filtered, = self.ax1.plot([], [], 'g-', label='Filtered Signal', linewidth=2)
        self.line_smoothed, = self.ax1.plot([], [], 'orange', label='Smoothed Signal', linewidth=1.5)
        self.ax1.set_ylabel('Amplitude (mV)')
        self.ax1.set_title('Signal Processing Pipeline')
        self.ax1.legend()
        self.ax1.grid(True, alpha=0.3)
        
        # Plot 2: Baseline tracking
        self.line_signal, = self.ax2.plot([], [], 'g-', label='Filtered Signal', linewidth=2)
        self.line_baseline, = self.ax2.plot([], [], 'r--', label='Adaptive Baseline', linewidth=2)
        self.ax2.set_ylabel('Amplitude (mV)')
        self.ax2.set_title('Baseline Tracking')
        self.ax2.legend()
        self.ax2.grid(True, alpha=0.3)
        
        # Plot 3: Detection with adaptive threshold
        self.line_corrected, = self.ax3.plot([], [], 'purple', label='Baseline Corrected', linewidth=2)
        self.line_thresh_pos, = self.ax3.plot([], [], 'r--', label='Adaptive Threshold', linewidth=2)
        self.line_thresh_neg, = self.ax3.plot([], [], 'r--', linewidth=2)
        self.ax3.set_ylabel('Amplitude (mV)')
        self.ax3.set_xlabel('Time (seconds)')
        self.ax3.set_title('Moderate Blink Detection with Adaptive Threshold')
        self.ax3.legend()
        self.ax3.grid(True, alpha=0.3)
        
        # Detection markers
        self.blink_markers = []
        
    def update_plot(self, frame):
        global blink_detected_flag, variance_buffer
        
        with data_lock:
            if len(timestamps_plot) < 10:
                return (self.line_raw, self.line_filtered, self.line_smoothed, 
                       self.line_signal, self.line_baseline, self.line_corrected)
            
            # Convert timestamps to relative seconds
            times = np.array(list(timestamps_plot))
            raw_data = np.array(list(plot_buffer_raw))
            filtered_data = np.array(list(plot_buffer_filtered))
            smoothed_data = np.array(list(plot_buffer_smoothed))
            baseline_data = np.array(list(plot_buffer_baseline))
            
            # Calculate current adaptive threshold
            current_variance = calculate_signal_variance(list(variance_buffer)) if len(variance_buffer) > 50 else 1.0
            adaptive_thresh = max(BLINK_THRESH, current_variance * ADAPTIVE_THRESH_FACTOR)
            adaptive_thresh = min(adaptive_thresh, BLINK_THRESH * 2.0)
            
            # Check for blink detection
            if blink_detected_flag:
                self.mark_blink(adaptive_thresh)
                blink_detected_flag = False
        
        if len(times) > 0:
            times = times - times[-1]  # Relative to current time
        
        # Update signal processing plots
        self.line_raw.set_data(times, raw_data)
        self.line_filtered.set_data(times, filtered_data)
        self.line_smoothed.set_data(times, smoothed_data)
        
        # Update baseline tracking
        self.line_signal.set_data(times, filtered_data)
        self.line_baseline.set_data(times, baseline_data)
        
        # Update detection plot
        corrected_data = filtered_data - baseline_data
        self.line_corrected.set_data(times, corrected_data)
        
        # Update adaptive threshold lines
        if len(times) > 0:
            thresh_pos = np.full_like(times, adaptive_thresh)
            thresh_neg = np.full_like(times, -adaptive_thresh)
            self.line_thresh_pos.set_data(times, thresh_pos)
            self.line_thresh_neg.set_data(times, thresh_neg)
        
        # Auto-scale axes
        for ax in [self.ax1, self.ax2, self.ax3]:
            ax.relim()
            ax.autoscale_view()
            if len(times) > 0:
                ax.set_xlim(-PLOT_WINDOW_SEC, 0)
        
        return (self.line_raw, self.line_filtered, self.line_smoothed, 
                self.line_signal, self.line_baseline, self.line_corrected)
    
    def mark_blink(self, threshold):
        """Add a blink detection marker with threshold info"""
        current_time = 0  # Will be at the right edge
        for ax in [self.ax1, self.ax2, self.ax3]:
            marker = ax.axvline(current_time, color='red', linestyle='-', linewidth=3, alpha=0.8)
            self.blink_markers.append(marker)
        
        # Add threshold text
        self.ax3.text(current_time, threshold + 0.5, f'{threshold:.1f}mV', 
                     color='red', fontsize=8, ha='center')
        
        # Remove old markers (keep only last 10)
        if len(self.blink_markers) > 20:
            for marker in self.blink_markers[:10]:
                marker.remove()
            self.blink_markers = self.blink_markers[10:]

# ——— ENHANCED NOTIFICATION HANDLER ———————————————————————————————

def notification_handler(sender, data: bytearray):
    """Enhanced notification handler with improved signal processing"""
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
            variance_buffer.append(eog_raw_mv)
            
            # Only process when we have enough data for meaningful filtering
            if len(signal_buffer) < MIN_LENGTH_FOR_FILTER:
                plot_buffer_filtered.append(eog_raw_mv)  # Use raw until filtered available
                plot_buffer_smoothed.append(eog_raw_mv)
                plot_buffer_baseline.append(0.0)
                return
            
            # Apply enhanced filtering
            signal_array = np.array(list(signal_buffer))
            filtered_signal = apply_enhanced_eog_filter(signal_array, FS)
            
            # Calculate adaptive baseline
            baseline = calculate_adaptive_baseline(list(baseline_buffer))
            
            # Calculate signal variance for adaptive thresholding
            signal_variance = calculate_signal_variance(list(variance_buffer))
            
            # Store for plotting
            plot_buffer_filtered.append(filtered_signal[-1])
            plot_buffer_smoothed.append(filtered_signal[-1])  # Same as filtered after smoothing
            plot_buffer_baseline.append(baseline)
            
            # Detection (every sample for most responsive detection)
            blink_detected = detect_moderate_blink(filtered_signal, baseline, signal_variance, FS)
            
            # Logging (every 20th sample to avoid spam but stay informative)
            if len(signal_buffer) % 20 == 0:
                baseline_corrected = abs(filtered_signal[-1] - baseline)
                adaptive_thresh = max(BLINK_THRESH, signal_variance * ADAPTIVE_THRESH_FACTOR)
                adaptive_thresh = min(adaptive_thresh, BLINK_THRESH * 2.0)
                
                logging.info(
                    f"EOG: raw={eog_raw_mv:.2f}mV | filtered={filtered_signal[-1]:.2f}mV | "
                    f"baseline={baseline:.2f}mV | corrected={baseline_corrected:.2f}mV | "
                    f"thresh={adaptive_thresh:.2f}mV | variance={signal_variance:.2f}"
                )
            
            if blink_detected:
                baseline_corrected = abs(filtered_signal[-1] - baseline)
                adaptive_thresh = max(BLINK_THRESH, signal_variance * ADAPTIVE_THRESH_FACTOR)
                adaptive_thresh = min(adaptive_thresh, BLINK_THRESH * 2.0)
                logging.info(f"\n\n\n\n\n\n\n\n\n\n\n\n 🎉 MAIN DETECTION CONFIRMED! Amplitude: {baseline_corrected:.2f}mV (Threshold: {adaptive_thresh:.2f}mV)\n\n\n\n\n\n\n\n\n\n\n\n")
                    
    except Exception as e:
        logging.error(f"Error in notification handler: {e}")
        # Fallback: use raw signal for plotting
        if 'eog_raw_mv' in locals():
            with data_lock:
                plot_buffer_filtered.append(eog_raw_mv)
                plot_buffer_smoothed.append(eog_raw_mv)
                plot_buffer_baseline.append(0.0)

# ——— BLE CONNECTION FUNCTIONS (unchanged) ———————————————————————

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
        logging.info("📡 Streaming EOG with enhanced visual monitoring.")

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
    logging.info("🚀 Starting Enhanced EOG Monitor with Moderate Blink Detection")
    logging.info(f"📊 Sampling Rate: {FS} Hz")
    logging.info(f"🔧 Bandpass Filter: {EOG_HIGHPASS}-{EOG_LOWPASS} Hz")
    logging.info(f"⚡ Notch Filter: {NOTCH_F} Hz (Q={NOTCH_Q})")
    logging.info(f"👁️  Base Blink Threshold: {BLINK_THRESH} mV")
    logging.info(f"🔄 Adaptive Threshold Factor: {ADAPTIVE_THRESH_FACTOR}")
    logging.info(f"⏰ Detection Cooldown: {DETECTION_COOLDOWN} seconds")
    logging.info(f"🎛️  Savitzky-Golay Smoothing: window={SAVGOL_WINDOW}, poly={SAVGOL_POLYORDER}")
    
    # Start BLE connection in background thread
    ble_thread = threading.Thread(target=run_ble_async, daemon=True)
    ble_thread.start()
    
    # Give BLE time to connect
    time.sleep(3)
    
    # Set up enhanced plotting on main thread
    plotter = EnhancedEOGPlotter()
    
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
    logging.info("📊 Starting enhanced plot - Close window to stop.")
    plt.show()
    
    # Clean up
    ble_connected = False
    logging.info("✅ Application closed.")

if __name__ == '__main__':
    main()