#!/usr/bin/env python3
"""
EOG Visual Filter with Simple Multi-Blink Detection
- Uses the original reliable hard blink detection
- Simple sequence counting: 2 blinks in 3s, 3 blinks in 5s
- Maintains all the good filtering and detection from original
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

# HARD BLINK THRESHOLDS - keep the original reliable settings
# HARD_BLINK_THRESH = 6.0      # 6mV threshold for hard blinks (same as original)
# HARD_BLINK_THRESH = 8.0      # 10mV threshold for hard blinks (increasing for hard blinks)
HARD_BLINK_THRESH = 5.0      # 10mV threshold for hard blinks (increasing for hard blinks)
BLINK_MIN_SAMPLES = int(FS * 0.05)   # 50ms minimum duration
BLINK_MAX_SAMPLES = int(FS * 0.6)    # 600ms maximum duration
BASELINE_WINDOW = int(FS * 2.5)      # 2.5 second baseline calculation

# Detection parameters - keep original reliable settings
# DETECTION_COOLDOWN = 0.8     # 800ms cooldown (original setting)
DETECTION_COOLDOWN = 0.5     # 800ms cooldown (original setting)
MIN_PEAK_RATIO = 1.5         # Peak must be 50% higher than surrounding signal
VALIDATION_STRICTNESS = 0.7  # 70% of blink must be above threshold * 0.8

# SIMPLE MULTI-BLINK DETECTION - much simpler timing
DOUBLE_BLINK_WINDOW = 3.0    # 3 seconds to complete double blink
TRIPLE_BLINK_WINDOW = 5.0    # 5 seconds to complete triple blink
PATTERN_DECISION_DELAY = 2.0 # Wait 2s after last blink to decide pattern

# Smoothing parameters (keep the noise reduction features)
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

# Simple multi-blink tracking
last_detection_time = 0
baseline_buffer = deque(maxlen=BASELINE_WINDOW)
blink_history = []  # Simple list of blink timestamps
pending_pattern_timer = None

# Threading control
data_lock = threading.Lock()
ble_client = None
ble_connected = False
blink_detected_flag = False
pattern_detected_flag = {'type': None, 'count': 0}

# ——— SIMPLE PATTERN DETECTION ——————————————————————————————————

def add_blink_to_history(timestamp):
    """Add blink to history and check for patterns"""
    global blink_history, pending_pattern_timer
    
    blink_history.append(timestamp)
    
    # Remove old blinks outside our detection windows
    current_time = time.time()
    blink_history = [t for t in blink_history if current_time - t <= TRIPLE_BLINK_WINDOW]
    
    logging.info(f"📝 Blink added to history. Total recent blinks: {len(blink_history)}")
    
    # Cancel any existing pattern timer
    if pending_pattern_timer:
        pending_pattern_timer.cancel()
    
    # Set new timer to analyze pattern after delay
    pending_pattern_timer = threading.Timer(PATTERN_DECISION_DELAY, analyze_blink_pattern)
    pending_pattern_timer.start()

def analyze_blink_pattern():
    """Analyze blink history and determine pattern"""
    global blink_history, pattern_detected_flag
    
    current_time = time.time()
    
    # Clean up old blinks
    blink_history = [t for t in blink_history if current_time - t <= TRIPLE_BLINK_WINDOW]
    
    blink_count = len(blink_history)
    
    if blink_count == 0:
        return blink_count
    
    # Check for triple blink (3 blinks within 5 seconds)
    if blink_count >= 3:
        # Check if the 3 most recent blinks are within 5 seconds
        recent_three = sorted(blink_history)[-3:]
        if recent_three[-1] - recent_three[0] <= TRIPLE_BLINK_WINDOW:
            pattern_detected_flag = {'type': 'triple', 'count': 3}
            logging.info("\n\n🔴🔴🔴 TRIPLE BLINK COMMAND DETECTED! 🔴🔴🔴")
            logging.info(f"   Timing: {recent_three[-1] - recent_three[0]:.1f}s window")
            logging.info("   → ADVANCED COMMAND TRIGGERED\n")
            blink_history.clear()
            return blink_count
    
    # Check for double blink (2 blinks within 3 seconds)
    if blink_count >= 2:
        # Check if the 2 most recent blinks are within 3 seconds
        recent_two = sorted(blink_history)[-2:]
        if recent_two[-1] - recent_two[0] <= DOUBLE_BLINK_WINDOW:
            pattern_detected_flag = {'type': 'double', 'count': 2}
            logging.info("\n\n🟡🟡 DOUBLE BLINK COMMAND DETECTED! 🟡🟡")
            logging.info(f"   Timing: {recent_two[-1] - recent_two[0]:.1f}s window")
            logging.info("   → SECONDARY COMMAND TRIGGERED\n")
            blink_history.clear()
            return blink_count
    
    # Single blink (only if no multiple blinks detected)
    if blink_count == 1:
        pattern_detected_flag = {'type': 'single', 'count': 1}
        logging.info("\n\n🟢 SINGLE BLINK COMMAND DETECTED! 🟢")
        logging.info("   → PRIMARY COMMAND TRIGGERED\n")
        blink_history.clear()
        return blink_count

# ——— ORIGINAL FILTERING FUNCTIONS (unchanged) ———————————————————

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

def calculate_robust_baseline(data):
    """Calculate robust baseline for hard blink detection"""
    if len(data) < 20:
        return 0.0
    
    # Use recent data but remove extreme outliers
    recent_data = np.array(data[-BASELINE_WINDOW//2:])
    
    # Simple percentile-based baseline (more stable than median with outliers)
    return np.percentile(recent_data, 50)  # 50th percentile (median)

def detect_hard_blinks_original(filtered_signal, baseline, fs):
    """Original reliable hard blink detection - UNCHANGED from working version"""
    global last_detection_time, blink_detected_flag
    
    current_time = time.time()
    
    # Cooldown check
    if current_time - last_detection_time < DETECTION_COOLDOWN:
        return False
    
    if len(filtered_signal) < BLINK_MIN_SAMPLES * 2:  # Need extra samples for validation
        return False
    
    # Baseline-corrected signal
    corrected_signal = filtered_signal - baseline
    current_amplitude = abs(corrected_signal[-1])
    
    # Simple threshold check - must be a clear hard blink
    if current_amplitude > HARD_BLINK_THRESH:
        
        # Validate by checking recent samples (last 100ms)
        validation_samples = int(FS * 0.1)  # 100ms
        recent_signal = corrected_signal[-validation_samples:]
        
        # Count samples above 80% of threshold
        high_samples = np.sum(np.abs(recent_signal) > HARD_BLINK_THRESH * 0.8)
        required_high_samples = int(validation_samples * VALIDATION_STRICTNESS)
        
        if high_samples >= required_high_samples:
            # Additional check: make sure this is a clear peak
            max_recent = np.max(np.abs(recent_signal))
            mean_recent = np.mean(np.abs(recent_signal))
            
            if max_recent / (mean_recent + 0.1) > MIN_PEAK_RATIO:  # Clear peak
                last_detection_time = current_time
                blink_detected_flag = True
                
                logging.info(f"🔥 HARD BLINK DETECTED!")
                logging.info(f"   Current amplitude: {current_amplitude:.2f}mV")
                logging.info(f"   Peak amplitude: {max_recent:.2f}mV") 
                logging.info(f"   Threshold: {HARD_BLINK_THRESH:.2f}mV")
                
                # Add to simple pattern tracking
                add_blink_to_history(current_time)
                
                return True
            else:
                logging.debug(f"❌ Rejected: peak ratio {max_recent/(mean_recent + 0.1):.2f} < {MIN_PEAK_RATIO}")
        else:
            logging.debug(f"❌ Rejected: only {high_samples}/{validation_samples} high samples (need {required_high_samples})")
    
    return False

# ——— ENHANCED PLOTTING SETUP ———————————————————————————————————

class SimpleMultiBlinkPlotter:
    def __init__(self):
        plt.style.use('default')
        self.fig, ((self.ax1, self.ax2), (self.ax3, self.ax4)) = plt.subplots(2, 2, figsize=(16, 12))
        self.fig.suptitle('EOG Multi-Blink Commands: 1 Blink | 2 in 3s | 3 in 5s', fontsize=16)
        
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
        
        # Plot 3: Detection with threshold
        self.line_corrected, = self.ax3.plot([], [], 'purple', label='Baseline Corrected', linewidth=2)
        self.line_thresh_pos, = self.ax3.plot([], [], 'r--', label=f'Hard Blink Threshold (±{HARD_BLINK_THRESH}mV)', linewidth=2)
        self.line_thresh_neg, = self.ax3.plot([], [], 'r--', linewidth=2)
        self.ax3.set_ylabel('Amplitude (mV)')
        self.ax3.set_xlabel('Time (seconds)')
        self.ax3.set_title('Hard Blink Detection')
        self.ax3.legend()
        self.ax3.grid(True, alpha=0.3)
        
        # Plot 4: Command Status
        self.ax4.clear()
        self.ax4.set_xlim(0, 10)
        self.ax4.set_ylim(0, 4)
        self.ax4.set_title('Command Status')
        self.ax4.set_yticks([1, 2, 3])
        self.ax4.set_yticklabels(['🟢 Single', '🟡 Double', '🔴 Triple'])
        self.ax4.grid(True, alpha=0.3)
        
        # Command status display
        self.command_text = self.ax4.text(5, 2, 'Ready for commands...', 
                                         ha='center', va='center', fontsize=14, 
                                         bbox=dict(boxstyle="round,pad=0.3", facecolor="lightblue"))
        
        # Blink history display
        self.history_text = self.ax4.text(5, 0.5, f'Recent blinks: 0', 
                                         ha='center', va='center', fontsize=10)
        
        # Detection markers for other plots
        self.blink_markers = []
        
    def update_plot(self, frame):
        global blink_detected_flag, pattern_detected_flag, blink_history
        
        with data_lock:
            if len(timestamps_plot) < 10:
                return tuple()
            
            # Convert timestamps to relative seconds
            times = np.array(list(timestamps_plot))
            raw_data = np.array(list(plot_buffer_raw))
            filtered_data = np.array(list(plot_buffer_filtered))
            smoothed_data = np.array(list(plot_buffer_smoothed))
            baseline_data = np.array(list(plot_buffer_baseline))
            
            # Check for blink detection
            if blink_detected_flag:
                self.mark_blink()
                blink_detected_flag = False
            
            # Check for pattern detection
            if pattern_detected_flag['type']:
                self.show_command(pattern_detected_flag['type'], pattern_detected_flag['count'])
                pattern_detected_flag = {'type': None, 'count': 0}
            
            # Update blink history display
            current_time = time.time()
            recent_blinks = [t for t in blink_history if current_time - t <= TRIPLE_BLINK_WINDOW]
            self.history_text.set_text(f'Recent blinks: {len(recent_blinks)}')
        
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
        
        # Update threshold lines
        if len(times) > 0:
            thresh_pos = np.full_like(times, HARD_BLINK_THRESH)
            thresh_neg = np.full_like(times, -HARD_BLINK_THRESH)
            self.line_thresh_pos.set_data(times, thresh_pos)
            self.line_thresh_neg.set_data(times, thresh_neg)
        
        # Auto-scale axes (first 3 plots)
        for ax in [self.ax1, self.ax2, self.ax3]:
            ax.relim()
            ax.autoscale_view()
            if len(times) > 0:
                ax.set_xlim(-PLOT_WINDOW_SEC, 0)
        
        return tuple()
    
    def mark_blink(self):
        """Add a blink detection marker"""
        current_time = 0  # Will be at the right edge
        for ax in [self.ax1, self.ax2, self.ax3]:
            marker = ax.axvline(current_time, color='blue', linestyle='-', linewidth=2, alpha=0.8)
            self.blink_markers.append(marker)
        
        # Remove old markers (keep only last 15)
        if len(self.blink_markers) > 30:
            for marker in self.blink_markers[:15]:
                marker.remove()
            self.blink_markers = self.blink_markers[15:]
    
    def show_command(self, command_type, count):
        """Show detected command"""
        commands = {
            'single': {'text': '🟢 SINGLE BLINK\nPRIMARY COMMAND', 'color': 'lightgreen'},
            'double': {'text': '🟡 DOUBLE BLINK\nSECONDARY COMMAND', 'color': 'lightyellow'},
            'triple': {'text': '🔴 TRIPLE BLINK\nADVANCED COMMAND', 'color': 'lightcoral'}
        }
        
        cmd = commands.get(command_type, {'text': 'UNKNOWN', 'color': 'lightgray'})
        
        self.command_text.set_text(cmd['text'])
        self.command_text.set_bbox(dict(boxstyle="round,pad=0.5", facecolor=cmd['color']))
        
        # Clear after 3 seconds
        threading.Timer(3.0, self.clear_command_display).start()
    
    def clear_command_display(self):
        """Clear command display"""
        self.command_text.set_text('Ready for commands...')
        self.command_text.set_bbox(dict(boxstyle="round,pad=0.3", facecolor="lightblue"))

# ——— NOTIFICATION HANDLER (mostly unchanged) ————————————————————

def notification_handler(sender, data: bytearray):
    """Enhanced notification handler - uses original detection"""
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
                plot_buffer_smoothed.append(eog_raw_mv)
                plot_buffer_baseline.append(0.0)
                return
            
            # Apply enhanced filtering (keep the good noise reduction)
            signal_array = np.array(list(signal_buffer))
            filtered_signal = apply_enhanced_eog_filter(signal_array, FS)
            
            # Calculate robust baseline
            baseline = calculate_robust_baseline(list(baseline_buffer))
            
            # Store for plotting
            plot_buffer_filtered.append(filtered_signal[-1])
            plot_buffer_smoothed.append(filtered_signal[-1])
            plot_buffer_baseline.append(baseline)
            
            # Use original hard blink detection (every sample for responsiveness)
            blink_detected = detect_hard_blinks_original(filtered_signal, baseline, FS)
            
            # Logging (every 60th sample to reduce spam)
            if len(signal_buffer) % 60 == 0:
                baseline_corrected = abs(filtered_signal[-1] - baseline)
                
                logging.debug(
                    f"EOG: raw={eog_raw_mv:.2f}mV | filtered={filtered_signal[-1]:.2f}mV | "
                    f"baseline={baseline:.2f}mV | corrected={baseline_corrected:.2f}mV | "
                    f"thresh={HARD_BLINK_THRESH:.2f}mV"
                )
                    
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
        ble_client = BleakClient(ADDRESS, timeout=100.0)
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
        logging.info("📡 Streaming EOG with simple multi-blink detection.")

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
    """Main function with simple multi-blink detection"""
    logging.info("🚀 Starting EOG with Simple Multi-Blink Commands")
    logging.info(f"📊 Sampling Rate: {FS} Hz")
    logging.info(f"🔥 Hard Blink Threshold: {HARD_BLINK_THRESH} mV (original reliable setting)")
    logging.info(f"⏰ Detection Cooldown: {DETECTION_COOLDOWN} seconds")
    logging.info("\n🎯 SIMPLE COMMAND PATTERNS:")
    logging.info(f"   🟢 Single Blink → PRIMARY COMMAND")
    logging.info(f"   🟡 Double Blink (within {DOUBLE_BLINK_WINDOW}s) → SECONDARY COMMAND")
    logging.info(f"   🔴 Triple Blink (within {TRIPLE_BLINK_WINDOW}s) → ADVANCED COMMAND")
    logging.info(f"   ⏱️  Pattern decision delay: {PATTERN_DECISION_DELAY}s after last blink")
    
    # Start BLE connection in background thread
    ble_thread = threading.Thread(target=run_ble_async, daemon=True)
    ble_thread.start()
    
    # Give BLE time to connect
    time.sleep(3)
    
    # Set up plotting on main thread
    plotter = SimpleMultiBlinkPlotter()
    
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
    logging.info("📊 Ready for multi-blink commands!")
    logging.info("💡 Try: Single hard blink, or 2 blinks in 3s, or 3 blinks in 5s")
    plt.show()
    
    # Clean up
    ble_connected = False
    logging.info("✅ Application closed.")

if __name__ == '__main__':
    main()