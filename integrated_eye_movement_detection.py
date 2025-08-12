#!/usr/bin/env python3
"""
EOG Visual Filter with Multi-Blink + Horizontal Eye Movement Detection
- Hard blink detection (vertical EOG): 1, 2, or 3 blinks for commands
- Horizontal saccade detection: Left/Right eye movements
- Different thresholds and processing for each movement type
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

# Enhanced filtering parameters
NOTCH_F = 60.0               # mains notch freq (Hz)
NOTCH_Q = 35.0               # Higher Q for sharper notch
EOG_LOWPASS = 35.0           # Lowpass for blinks
EOG_HIGHPASS = 0.05          # Highpass for blinks
FILTER_ORDER = 4             # Filter order

# Saccade-specific filtering (different from blinks)
SACCADE_LOWPASS = 15.0       # Lower cutoff for saccades (slower movements)
SACCADE_HIGHPASS = 0.1       # Slightly higher to remove very slow drift

# Buffer sizes
BUFFER_SEC = 1.0
BUFFER_SIZE = int(FS * BUFFER_SEC)
PLOT_BUFFER_SIZE = int(FS * PLOT_WINDOW_SEC)

# HARD BLINK THRESHOLDS (keep original reliable settings)
HARD_BLINK_THRESH = 6.0      # 6mV threshold for hard blinks
BLINK_MIN_SAMPLES = int(FS * 0.05)   # 50ms minimum duration
BLINK_MAX_SAMPLES = int(FS * 0.6)    # 600ms maximum duration
BASELINE_WINDOW = int(FS * 2.5)      # 2.5 second baseline calculation

# HORIZONTAL SACCADE THRESHOLDS (new)
LEFT_SACCADE_THRESH = -2.5   # Negative threshold for left movement (mV)
RIGHT_SACCADE_THRESH = 2.5   # Positive threshold for right movement (mV)
SACCADE_MIN_SAMPLES = int(FS * 0.1)   # 100ms minimum duration (longer than blinks)
SACCADE_MAX_SAMPLES = int(FS * 2.0)   # 2s maximum duration
SACCADE_BASELINE_WINDOW = int(FS * 5.0)  # 5 second baseline for saccades

# Detection parameters - original for blinks
BLINK_DETECTION_COOLDOWN = 0.8     # 800ms cooldown for blinks
SACCADE_DETECTION_COOLDOWN = 1.0   # 1s cooldown for saccades
MIN_PEAK_RATIO = 1.5         # Peak ratio for validation
VALIDATION_STRICTNESS = 0.7  # Validation strictness

# Saccade-specific validation
SACCADE_VALIDATION_STRICTNESS = 0.6  # Slightly more lenient for saccades
SACCADE_MIN_PEAK_RATIO = 1.3         # Lower ratio for saccades (weaker signal)

# Multi-blink timing (keep simple)
DOUBLE_BLINK_WINDOW = 3.0    # 3 seconds to complete double blink
TRIPLE_BLINK_WINDOW = 5.0    # 5 seconds to complete triple blink
PATTERN_DECISION_DELAY = 2.0 # Wait 2s after last blink to decide pattern

# Smoothing parameters
SAVGOL_WINDOW = 15
SAVGOL_POLYORDER = 3

# Minimum signal lengths for filtering
MIN_LENGTH_FOR_FILTER = 50
MIN_LENGTH_FOR_NOTCH = 25

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
plot_buffer_saccade_filtered = deque(maxlen=PLOT_BUFFER_SIZE)  # New: saccade-filtered signal
plot_buffer_baseline = deque(maxlen=PLOT_BUFFER_SIZE)
plot_buffer_saccade_baseline = deque(maxlen=PLOT_BUFFER_SIZE)  # New: saccade baseline
timestamps_plot = deque(maxlen=PLOT_BUFFER_SIZE)

# Detection state
last_blink_detection_time = 0
last_saccade_detection_time = 0
baseline_buffer = deque(maxlen=BASELINE_WINDOW)
saccade_baseline_buffer = deque(maxlen=SACCADE_BASELINE_WINDOW)
blink_history = []
pending_pattern_timer = None

# Threading control
data_lock = threading.Lock()
ble_client = None
ble_connected = False

# Detection flags
blink_detected_flag = False
saccade_detected_flag = {'direction': None, 'amplitude': 0}
pattern_detected_flag = {'type': None, 'count': 0}

# ——— FILTERING FUNCTIONS ————————————————————————————————————————

def butter_bandpass(lowcut, highcut, fs, order=4):
    """Bandpass filter"""
    nyq = 0.5 * fs
    low = lowcut / nyq
    high = highcut / nyq
    b, a = butter(order, [low, high], btype='band')
    return b, a

def apply_notch(data, fs, f0=NOTCH_F, Q=NOTCH_Q):
    """Enhanced notch filter for 60Hz mains"""
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
        required_length = 3 * max(len(a), len(b))
        if len(data) <= required_length:
            return data
        return filtfilt(b, a, data)
    except ValueError as e:
        if "padlen" in str(e):
            logging.warning(f"Notch filter failed due to short signal length ({len(data)} samples)")
            return data
        else:
            raise e

def apply_savgol_smoothing(data, window=SAVGOL_WINDOW, polyorder=SAVGOL_POLYORDER):
    """Apply Savitzky-Golay smoothing"""
    if len(data) < window:
        return data
    
    if window % 2 == 0:
        window += 1
    
    window = min(window, len(data))
    if window < polyorder + 1:
        return data
        
    try:
        return savgol_filter(data, window, polyorder)
    except:
        return data

def apply_blink_filter(data, fs):
    """Filter optimized for blink detection (vertical EOG)"""
    if len(data) < 20:
        return data
    
    # Median filter for impulse noise
    data_median = medfilt(data, kernel_size=3)
    
    if len(data_median) < MIN_LENGTH_FOR_FILTER:
        return data_median
    
    try:
        # Bandpass filter for blinks
        b, a = butter_bandpass(EOG_HIGHPASS, EOG_LOWPASS, fs, order=FILTER_ORDER)
        
        required_length = 3 * max(len(a), len(b))
        if len(data_median) <= required_length:
            b, a = butter_bandpass(EOG_HIGHPASS, EOG_LOWPASS, fs, order=2)
            required_length = 3 * max(len(a), len(b))
            if len(data_median) <= required_length:
                return data_median
        
        data_bandpass = filtfilt(b, a, data_median)
        
        # 60Hz notch filter
        data_notched = apply_notch(data_bandpass, fs)
        
        # Savitzky-Golay smoothing
        data_smoothed = apply_savgol_smoothing(data_notched)
        
        return data_smoothed
        
    except ValueError as e:
        if "padlen" in str(e):
            logging.warning(f"Blink filter failed, using median filter only")
            return data_median
        else:
            logging.error(f"Unexpected error in blink filtering: {e}")
            return data_median

def apply_saccade_filter(data, fs):
    """Filter optimized for saccade detection (horizontal EOG)"""
    if len(data) < 20:
        return data
    
    # Less aggressive median filter (preserve signal shape)
    data_median = medfilt(data, kernel_size=3)
    
    if len(data_median) < MIN_LENGTH_FOR_FILTER:
        return data_median
    
    try:
        # Bandpass filter for saccades (different cutoffs)
        b, a = butter_bandpass(SACCADE_HIGHPASS, SACCADE_LOWPASS, fs, order=FILTER_ORDER)
        
        required_length = 3 * max(len(a), len(b))
        if len(data_median) <= required_length:
            b, a = butter_bandpass(SACCADE_HIGHPASS, SACCADE_LOWPASS, fs, order=2)
            required_length = 3 * max(len(a), len(b))
            if len(data_median) <= required_length:
                return data_median
        
        data_bandpass = filtfilt(b, a, data_median)
        
        # 60Hz notch filter
        data_notched = apply_notch(data_bandpass, fs)
        
        # Light smoothing (preserve saccade shape)
        data_smoothed = apply_savgol_smoothing(data_notched, window=9, polyorder=2)
        
        return data_smoothed
        
    except ValueError as e:
        if "padlen" in str(e):
            logging.warning(f"Saccade filter failed, using median filter only")
            return data_median
        else:
            logging.error(f"Unexpected error in saccade filtering: {e}")
            return data_median

def calculate_robust_baseline(data):
    """Calculate robust baseline"""
    if len(data) < 20:
        return 0.0
    
    recent_data = np.array(data[-len(data)//2:])
    return np.percentile(recent_data, 50)  # Median

# ——— BLINK DETECTION (original reliable method) ————————————————

def add_blink_to_history(timestamp):
    """Add blink to history and check for patterns"""
    global blink_history, pending_pattern_timer
    
    blink_history.append(timestamp)
    
    # Remove old blinks
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
    blink_history = [t for t in blink_history if current_time - t <= TRIPLE_BLINK_WINDOW]
    
    blink_count = len(blink_history)
    
    if blink_count == 0:
        return
    
    # Check for triple blink (3 blinks within 5 seconds)
    if blink_count >= 3:
        recent_three = sorted(blink_history)[-3:]
        if recent_three[-1] - recent_three[0] <= TRIPLE_BLINK_WINDOW:
            pattern_detected_flag = {'type': 'triple', 'count': 3}
            logging.info("\n🔴🔴🔴 TRIPLE BLINK COMMAND! 🔴🔴🔴")
            blink_history.clear()
            return
    
    # Check for double blink (2 blinks within 3 seconds)
    if blink_count >= 2:
        recent_two = sorted(blink_history)[-2:]
        if recent_two[-1] - recent_two[0] <= DOUBLE_BLINK_WINDOW:
            pattern_detected_flag = {'type': 'double', 'count': 2}
            logging.info("\n🟡🟡 DOUBLE BLINK COMMAND! 🟡🟡")
            blink_history.clear()
            return
    
    # Single blink
    if blink_count == 1:
        pattern_detected_flag = {'type': 'single', 'count': 1}
        logging.info("\n🟢 SINGLE BLINK COMMAND! 🟢")
        blink_history.clear()
        return

def detect_hard_blinks(filtered_signal, baseline, fs):
    """Original reliable hard blink detection"""
    global last_blink_detection_time, blink_detected_flag
    
    current_time = time.time()
    
    # Cooldown check
    if current_time - last_blink_detection_time < BLINK_DETECTION_COOLDOWN:
        return False
    
    if len(filtered_signal) < BLINK_MIN_SAMPLES * 2:
        return False
    
    # Baseline-corrected signal
    corrected_signal = filtered_signal - baseline
    current_amplitude = abs(corrected_signal[-1])
    
    # Threshold check
    if current_amplitude > HARD_BLINK_THRESH:
        
        # Validate by checking recent samples (last 100ms)
        validation_samples = int(FS * 0.1)  # 100ms
        recent_signal = corrected_signal[-validation_samples:]
        
        # Count samples above 80% of threshold
        high_samples = np.sum(np.abs(recent_signal) > HARD_BLINK_THRESH * 0.8)
        required_high_samples = int(validation_samples * VALIDATION_STRICTNESS)
        
        if high_samples >= required_high_samples:
            # Peak validation
            max_recent = np.max(np.abs(recent_signal))
            mean_recent = np.mean(np.abs(recent_signal))
            
            if max_recent / (mean_recent + 0.1) > MIN_PEAK_RATIO:
                last_blink_detection_time = current_time
                blink_detected_flag = True
                
                logging.info(f"🔥 HARD BLINK DETECTED!")
                logging.info(f"   Amplitude: {current_amplitude:.2f}mV")
                logging.info(f"   Peak: {max_recent:.2f}mV")
                
                # Add to pattern tracking
                add_blink_to_history(current_time)
                
                return True
    
    return False

# ——— SACCADE DETECTION (new) ———————————————————————————————————

def detect_horizontal_saccades(saccade_filtered_signal, saccade_baseline, fs):
    """Detect horizontal eye movements (left/right saccades)"""
    global last_saccade_detection_time, saccade_detected_flag
    
    current_time = time.time()
    
    # Cooldown check
    if current_time - last_saccade_detection_time < SACCADE_DETECTION_COOLDOWN:
        return False
    
    if len(saccade_filtered_signal) < SACCADE_MIN_SAMPLES * 2:
        return False
    
    # Baseline-corrected signal (preserve polarity!)
    corrected_signal = saccade_filtered_signal - saccade_baseline
    current_amplitude = corrected_signal[-1]  # Keep sign for direction
    
    # Check for left saccade (negative)
    if current_amplitude < LEFT_SACCADE_THRESH:
        
        # Validate by checking recent samples (last 200ms for saccades)
        validation_samples = int(FS * 0.2)  # 200ms
        recent_signal = corrected_signal[-validation_samples:]
        
        # Count samples below threshold (negative)
        low_samples = np.sum(recent_signal < LEFT_SACCADE_THRESH * 0.8)
        required_low_samples = int(validation_samples * SACCADE_VALIDATION_STRICTNESS)
        
        if low_samples >= required_low_samples:
            # Peak validation for left movement
            min_recent = np.min(recent_signal)
            mean_recent = np.mean(np.abs(recent_signal))
            
            if abs(min_recent) / (mean_recent + 0.1) > SACCADE_MIN_PEAK_RATIO:
                last_saccade_detection_time = current_time
                saccade_detected_flag = {'direction': 'left', 'amplitude': abs(current_amplitude)}
                
                logging.info(f"⬅️  LEFT SACCADE DETECTED!")
                logging.info(f"   Amplitude: {current_amplitude:.2f}mV")
                logging.info(f"   Peak: {min_recent:.2f}mV")
                logging.info(f"   Threshold: {LEFT_SACCADE_THRESH:.2f}mV")
                
                return True
    
    # Check for right saccade (positive)
    elif current_amplitude > RIGHT_SACCADE_THRESH:
        
        # Validate by checking recent samples (last 200ms for saccades)
        validation_samples = int(FS * 0.2)  # 200ms
        recent_signal = corrected_signal[-validation_samples:]
        
        # Count samples above threshold (positive)
        high_samples = np.sum(recent_signal > RIGHT_SACCADE_THRESH * 0.8)
        required_high_samples = int(validation_samples * SACCADE_VALIDATION_STRICTNESS)
        
        if high_samples >= required_high_samples:
            # Peak validation for right movement
            max_recent = np.max(recent_signal)
            mean_recent = np.mean(np.abs(recent_signal))
            
            if max_recent / (mean_recent + 0.1) > SACCADE_MIN_PEAK_RATIO:
                last_saccade_detection_time = current_time
                saccade_detected_flag = {'direction': 'right', 'amplitude': current_amplitude}
                
                logging.info(f"➡️  RIGHT SACCADE DETECTED!")
                logging.info(f"   Amplitude: {current_amplitude:.2f}mV")
                logging.info(f"   Peak: {max_recent:.2f}mV")
                logging.info(f"   Threshold: {RIGHT_SACCADE_THRESH:.2f}mV")
                
                return True
    
    return False

# ——— ENHANCED PLOTTING WITH SACCADE DETECTION ——————————————————

class MultiModalEOGPlotter:
    def __init__(self):
        plt.style.use('default')
        self.fig, ((self.ax1, self.ax2), (self.ax3, self.ax4)) = plt.subplots(2, 2, figsize=(18, 12))
        self.fig.suptitle('EOG Multi-Modal Detection: Blinks (1/2/3) + Saccades (L/R)', fontsize=16)
        
        # Plot 1: Raw vs Blink-filtered
        self.line_raw, = self.ax1.plot([], [], 'b-', alpha=0.7, label='Raw Signal', linewidth=1)
        self.line_blink_filtered, = self.ax1.plot([], [], 'g-', label='Blink Filtered', linewidth=2)
        self.line_saccade_filtered, = self.ax1.plot([], [], 'purple', label='Saccade Filtered', linewidth=1.5)
        self.ax1.set_ylabel('Amplitude (mV)')
        self.ax1.set_title('Signal Processing: Blink vs Saccade Filtering')
        self.ax1.legend()
        self.ax1.grid(True, alpha=0.3)
        
        # Plot 2: Blink detection
        self.line_blink_signal, = self.ax2.plot([], [], 'g-', label='Blink Signal', linewidth=2)
        self.line_blink_baseline, = self.ax2.plot([], [], 'r--', label='Blink Baseline', linewidth=2)
        self.line_blink_thresh, = self.ax2.plot([], [], 'orange', linestyle='--', 
                                               label=f'Blink Threshold ({HARD_BLINK_THRESH}mV)', linewidth=2)
        self.ax2.set_ylabel('Amplitude (mV)')
        self.ax2.set_title('Vertical EOG: Blink Detection')
        self.ax2.legend()
        self.ax2.grid(True, alpha=0.3)
        
        # Plot 3: Saccade detection
        self.line_saccade_signal, = self.ax3.plot([], [], 'purple', label='Saccade Signal', linewidth=2)
        self.line_saccade_baseline, = self.ax3.plot([], [], 'gray', linestyle='--', label='Saccade Baseline', linewidth=2)
        self.line_left_thresh, = self.ax3.plot([], [], 'red', linestyle='--', 
                                             label=f'Left Threshold ({LEFT_SACCADE_THRESH}mV)', linewidth=2)
        self.line_right_thresh, = self.ax3.plot([], [], 'blue', linestyle='--', 
                                              label=f'Right Threshold ({RIGHT_SACCADE_THRESH}mV)', linewidth=2)
        self.ax3.set_ylabel('Amplitude (mV)')
        self.ax3.set_xlabel('Time (seconds)')
        self.ax3.set_title('Horizontal EOG: Saccade Detection')
        self.ax3.legend()
        self.ax3.grid(True, alpha=0.3)
        
        # Plot 4: Command Status
        self.ax4.clear()
        self.ax4.set_xlim(0, 10)
        self.ax4.set_ylim(0, 6)
        self.ax4.set_title('Command Status')
        self.ax4.set_yticks([1, 2, 3, 4, 5])
        self.ax4.set_yticklabels(['🟢 Single', '🟡 Double', '🔴 Triple', '⬅️ Left', '➡️ Right'])
        self.ax4.grid(True, alpha=0.3)
        
        # Command status display
        self.command_text = self.ax4.text(5, 3, 'Ready for commands...', 
                                         ha='center', va='center', fontsize=14, 
                                         bbox=dict(boxstyle="round,pad=0.3", facecolor="lightblue"))
        
        # History displays
        self.blink_history_text = self.ax4.text(2, 0.5, f'Blinks: 0', ha='center', va='center', fontsize=10)
        self.saccade_history_text = self.ax4.text(8, 0.5, f'Last saccade: None', ha='center', va='center', fontsize=10)
        
        # Detection markers
        self.blink_markers = []
        self.saccade_markers = []
        
    def update_plot(self, frame):
        global blink_detected_flag, saccade_detected_flag, pattern_detected_flag, blink_history
        
        with data_lock:
            if len(timestamps_plot) < 10:
                return tuple()
            
            # Get data
            times = np.array(list(timestamps_plot))
            raw_data = np.array(list(plot_buffer_raw))
            blink_filtered_data = np.array(list(plot_buffer_filtered))
            saccade_filtered_data = np.array(list(plot_buffer_saccade_filtered))
            blink_baseline_data = np.array(list(plot_buffer_baseline))
            saccade_baseline_data = np.array(list(plot_buffer_saccade_baseline))
            
            # Check for detections
            if blink_detected_flag:
                self.mark_blink()
                blink_detected_flag = False
            
            if saccade_detected_flag['direction']:
                self.mark_saccade(saccade_detected_flag['direction'], saccade_detected_flag['amplitude'])
                saccade_detected_flag = {'direction': None, 'amplitude': 0}
            
            if pattern_detected_flag['type']:
                self.show_command(pattern_detected_flag['type'], pattern_detected_flag['count'])
                pattern_detected_flag = {'type': None, 'count': 0}
            
            # Update history displays
            current_time = time.time()
            recent_blinks = [t for t in blink_history if current_time - t <= TRIPLE_BLINK_WINDOW]
            self.blink_history_text.set_text(f'Recent blinks: {len(recent_blinks)}')
        
        if len(times) > 0:
            times = times - times[-1]  # Relative to current time
        
        # Update signal plots
        self.line_raw.set_data(times, raw_data)
        self.line_blink_filtered.set_data(times, blink_filtered_data)
        self.line_saccade_filtered.set_data(times, saccade_filtered_data)
        
        # Update blink detection plot
        self.line_blink_signal.set_data(times, blink_filtered_data)
        self.line_blink_baseline.set_data(times, blink_baseline_data)
        
        # Update saccade detection plot
        self.line_saccade_signal.set_data(times, saccade_filtered_data)
        self.line_saccade_baseline.set_data(times, saccade_baseline_data)
        
        # Update threshold lines
        if len(times) > 0:
            # Blink threshold
            blink_thresh = np.full_like(times, HARD_BLINK_THRESH) + blink_baseline_data
            self.line_blink_thresh.set_data(times, blink_thresh)
            
            # Saccade thresholds
            left_thresh = np.full_like(times, LEFT_SACCADE_THRESH) + saccade_baseline_data
            right_thresh = np.full_like(times, RIGHT_SACCADE_THRESH) + saccade_baseline_data
            self.line_left_thresh.set_data(times, left_thresh)
            self.line_right_thresh.set_data(times, right_thresh)
        
        # Auto-scale axes
        for ax in [self.ax1, self.ax2, self.ax3]:
            ax.relim()
            ax.autoscale_view()
            if len(times) > 0:
                ax.set_xlim(-PLOT_WINDOW_SEC, 0)
        
        return tuple()
    
    def mark_blink(self):
        """Add a blink detection marker"""
        current_time = 0
        for ax in [self.ax1, self.ax2]:
            marker = ax.axvline(current_time, color='red', linestyle='-', linewidth=3, alpha=0.8)
            self.blink_markers.append(marker)
        
        # Remove old markers
        if len(self.blink_markers) > 20:
            for marker in self.blink_markers[:10]:
                marker.remove()
            self.blink_markers = self.blink_markers[10:]
    
    def mark_saccade(self, direction, amplitude):
        """Add a saccade detection marker"""
        current_time = 0
        color = 'blue' if direction == 'right' else 'red'
        
        for ax in [self.ax1, self.ax3]:
            marker = ax.axvline(current_time, color=color, linestyle=':', linewidth=3, alpha=0.8)
            self.saccade_markers.append(marker)
        
        # Update saccade history
        self.saccade_history_text.set_text(f'Last: {direction.upper()} ({amplitude:.1f}mV)')
        
        # Remove old markers
        if len(self.saccade_markers) > 20:
            for marker in self.saccade_markers[:10]:
                marker.remove()
            self.saccade_markers = self.saccade_markers[10:]
    
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

# ——— NOTIFICATION HANDLER WITH DUAL PROCESSING ————————————————

def notification_handler(sender, data: bytearray):
    """Enhanced notification handler with blink + saccade detection"""
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
            saccade_baseline_buffer.append(eog_raw_mv)
            
            # Process when we have enough data
            if len(signal_buffer) < MIN_LENGTH_FOR_FILTER:
                plot_buffer_filtered.append(eog_raw_mv)
                plot_buffer_saccade_filtered.append(eog_raw_mv)
                plot_buffer_baseline.append(0.0)
                plot_buffer_saccade_baseline.append(0.0)
                return
            
            # Apply dual filtering
            signal_array = np.array(list(signal_buffer))
            
            # Blink-optimized filtering
            blink_filtered_signal = apply_blink_filter(signal_array, FS)
            blink_baseline = calculate_robust_baseline(list(baseline_buffer))
            
            # Saccade-optimized filtering  
            saccade_filtered_signal = apply_saccade_filter(signal_array, FS)
            saccade_baseline = calculate_robust_baseline(list(saccade_baseline_buffer))
            
            # Store for plotting
            plot_buffer_filtered.append(blink_filtered_signal[-1])
            plot_buffer_saccade_filtered.append(saccade_filtered_signal[-1])
            plot_buffer_baseline.append(blink_baseline)
            plot_buffer_saccade_baseline.append(saccade_baseline)
            
            # Run both detectors
            blink_detected = detect_hard_blinks(blink_filtered_signal, blink_baseline, FS)
            saccade_detected = detect_horizontal_saccades(saccade_filtered_signal, saccade_baseline, FS)
            
            # Reduced logging
            if len(signal_buffer) % 120 == 0:  # Every 120th sample (0.5s intervals)
                blink_corrected = abs(blink_filtered_signal[-1] - blink_baseline)
                saccade_corrected = saccade_filtered_signal[-1] - saccade_baseline
                
                logging.debug(
                    f"EOG: raw={eog_raw_mv:.2f}mV | "
                    f"blink_filtered={blink_filtered_signal[-1]:.2f}mV (corrected={blink_corrected:.2f}mV) | "
                    f"saccade_filtered={saccade_filtered_signal[-1]:.2f}mV (corrected={saccade_corrected:.2f}mV)"
                )
                    
    except Exception as e:
        logging.error(f"Error in notification handler: {e}")
        if 'eog_raw_mv' in locals():
            with data_lock:
                plot_buffer_filtered.append(eog_raw_mv)
                plot_buffer_saccade_filtered.append(eog_raw_mv)
                plot_buffer_baseline.append(0.0)
                plot_buffer_saccade_baseline.append(0.0)

# ——— BLE CONNECTION FUNCTIONS ——————————————————————————————————

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
        logging.info("📡 Streaming EOG with multi-modal detection (blinks + saccades).")

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
    """Main function with multi-modal EOG detection"""
    logging.info("🚀 Starting Multi-Modal EOG Detection")
    logging.info(f"📊 Sampling Rate: {FS} Hz")
    logging.info("\n🔥 BLINK DETECTION:")
    logging.info(f"   Threshold: {HARD_BLINK_THRESH} mV")
    logging.info(f"   Filter: {EOG_HIGHPASS}-{EOG_LOWPASS} Hz")
    logging.info(f"   Cooldown: {BLINK_DETECTION_COOLDOWN}s")
    logging.info("\n👁️ SACCADE DETECTION:")
    logging.info(f"   Left threshold: {LEFT_SACCADE_THRESH} mV")
    logging.info(f"   Right threshold: {RIGHT_SACCADE_THRESH} mV")
    logging.info(f"   Filter: {SACCADE_HIGHPASS}-{SACCADE_LOWPASS} Hz")
    logging.info(f"   Cooldown: {SACCADE_DETECTION_COOLDOWN}s")
    logging.info("\n🎯 COMMANDS:")
    logging.info("   🟢 Single blink → PRIMARY COMMAND")
    logging.info(f"   🟡 Double blink (within {DOUBLE_BLINK_WINDOW}s) → SECONDARY COMMAND")
    logging.info(f"   🔴 Triple blink (within {TRIPLE_BLINK_WINDOW}s) → ADVANCED COMMAND")
    logging.info("   ⬅️ Look left → LEFT COMMAND")
    logging.info("   ➡️ Look right → RIGHT COMMAND")
    
    # Start BLE connection in background thread
    ble_thread = threading.Thread(target=run_ble_async, daemon=True)
    ble_thread.start()
    
    # Give BLE time to connect
    time.sleep(3)
    
    # Set up plotting on main thread
    plotter = MultiModalEOGPlotter()
    
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
    logging.info("📊 Multi-modal EOG detection started!")
    logging.info("💡 Try: Hard blinks (1/2/3), Look left, Look right")
    plt.show()
    
    # Clean up
    ble_connected = False
    logging.info("✅ Application closed.")

if __name__ == '__main__':
    main()