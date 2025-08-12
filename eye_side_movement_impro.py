#!/usr/bin/env python3
"""
Horizontal EOG Movement Detection
- Detects left and right eye movements using differential thresholds
- Uses enhanced filtering from the blink detection system
- Tracks movement patterns and sequences
- Provides visual feedback for horizontal eye movements
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
PLOT_WINDOW_SEC = 15.0        # Show last 15 seconds for movement tracking
PLOT_UPDATE_MS = 50           # Faster updates for smoother movement tracking

# ——— SIGNAL CONFIG ——————————————————————————————————————————————
FS = 240.0                    # sampling rate (Hz)
VAFE_GAIN_LSB_PER_MV = 78     # From datasheet: 78 LSB/mV

# Enhanced filtering parameters optimized for horizontal movements
NOTCH_F = 60.0               # mains notch freq (Hz)
NOTCH_Q = 35.0               # Higher Q for sharper notch
EOG_LOWPASS = 15.0           # Lower cutoff for horizontal movements (slower than blinks)
EOG_HIGHPASS = 0.1           # Preserve slow movements
FILTER_ORDER = 4             # Keep reasonable order

# Buffer sizes
BUFFER_SEC = 2.0             # Longer buffer for movement analysis
BUFFER_SIZE = int(FS * BUFFER_SEC)
PLOT_BUFFER_SIZE = int(FS * PLOT_WINDOW_SEC)

# HORIZONTAL MOVEMENT DETECTION THRESHOLDS
MOVEMENT_THRESH_LOW = 2.0     # Lower threshold for subtle movements (mV)
MOVEMENT_THRESH_HIGH = 4.0    # Higher threshold for strong movements (mV)
MOVEMENT_MIN_DURATION = int(FS * 0.1)    # 100ms minimum duration
MOVEMENT_MAX_DURATION = int(FS * 2.0)    # 2s maximum duration
BASELINE_WINDOW = int(FS * 3.0)          # 3 second baseline calculation

# Detection parameters
DETECTION_COOLDOWN = 0.3     # 300ms cooldown between movements
MIN_MOVEMENT_RATIO = 1.2     # Movement must be 20% higher than noise
VALIDATION_STRICTNESS = 0.6  # 60% of movement must be above threshold

# MOVEMENT PATTERN DETECTION
SEQUENCE_TIMEOUT = 3.0       # 3 seconds to complete movement sequence
PATTERN_DECISION_DELAY = 1.5 # Wait 1.5s after last movement to decide pattern

# Direction detection parameters
DIRECTION_HYSTERESIS = 0.5   # mV hysteresis to avoid direction flipping
DIRECTION_MIN_SAMPLES = int(FS * 0.05)  # 50ms minimum for direction decision

# Smoothing parameters
SAVGOL_WINDOW = 21           # Larger window for smoother movement tracking
SAVGOL_POLYORDER = 3         # Polynomial order for smoothing

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
plot_buffer_baseline = deque(maxlen=PLOT_BUFFER_SIZE)
plot_buffer_smoothed = deque(maxlen=PLOT_BUFFER_SIZE)
plot_buffer_derivative = deque(maxlen=PLOT_BUFFER_SIZE)
timestamps_plot = deque(maxlen=PLOT_BUFFER_SIZE)

# Movement tracking
last_detection_time = 0
baseline_buffer = deque(maxlen=BASELINE_WINDOW)
movement_history = []  # List of movement events: {'time': float, 'direction': str, 'amplitude': float}
pending_pattern_timer = None

# Threading control
data_lock = threading.Lock()
ble_client = None
ble_connected = False
movement_detected_flag = {'detected': False, 'direction': None, 'amplitude': 0}
pattern_detected_flag = {'type': None, 'sequence': []}

# Movement state tracking
current_direction = None
direction_start_time = 0
last_baseline = 0

# ——— MOVEMENT PATTERN DETECTION ——————————————————————————————————

def add_movement_to_history(timestamp, direction, amplitude):
    """Add movement to history and check for patterns"""
    global movement_history, pending_pattern_timer
    
    movement_history.append({
        'time': timestamp,
        'direction': direction,
        'amplitude': amplitude
    })
    
    # Remove old movements outside our detection window
    current_time = time.time()
    movement_history = [m for m in movement_history if current_time - m['time'] <= SEQUENCE_TIMEOUT]
    
    logging.info(f"👁️  Movement added: {direction.upper()} ({amplitude:.2f}mV). Recent movements: {len(movement_history)}")
    
    # Cancel any existing pattern timer
    if pending_pattern_timer:
        pending_pattern_timer.cancel()
    
    # Set new timer to analyze pattern after delay
    pending_pattern_timer = threading.Timer(PATTERN_DECISION_DELAY, analyze_movement_pattern)
    pending_pattern_timer.start()

def analyze_movement_pattern():
    """Analyze movement history and determine patterns"""
    global movement_history, pattern_detected_flag
    
    current_time = time.time()
    
    # Clean up old movements
    movement_history = [m for m in movement_history if current_time - m['time'] <= SEQUENCE_TIMEOUT]
    
    if not movement_history:
        return
    
    # Sort by time
    movement_history.sort(key=lambda x: x['time'])
    
    # Extract sequence
    sequence = [m['direction'] for m in movement_history]
    
    # Detect specific patterns
    if len(sequence) >= 4:
        # Look for left-right-left-right or right-left-right-left patterns
        if sequence[-4:] == ['left', 'right', 'left', 'right']:
            pattern_detected_flag = {'type': 'alternating_lr', 'sequence': sequence[-4:]}
            logging.info("\n\n🔄🔄 ALTERNATING L-R-L-R PATTERN DETECTED! 🔄🔄")
            logging.info("   → COMPLEX NAVIGATION COMMAND\n")
            movement_history.clear()
            return
        elif sequence[-4:] == ['right', 'left', 'right', 'left']:
            pattern_detected_flag = {'type': 'alternating_rl', 'sequence': sequence[-4:]}
            logging.info("\n\n🔄🔄 ALTERNATING R-L-R-L PATTERN DETECTED! 🔄🔄")
            logging.info("   → COMPLEX NAVIGATION COMMAND\n")
            movement_history.clear()
            return
    
    if len(sequence) >= 3:
        # Triple left or triple right
        if sequence[-3:] == ['left', 'left', 'left']:
            pattern_detected_flag = {'type': 'triple_left', 'sequence': sequence[-3:]}
            logging.info("\n\n⬅️⬅️⬅️ TRIPLE LEFT DETECTED! ⬅️⬅️⬅️")
            logging.info("   → STRONG LEFT COMMAND\n")
            movement_history.clear()
            return
        elif sequence[-3:] == ['right', 'right', 'right']:
            pattern_detected_flag = {'type': 'triple_right', 'sequence': sequence[-3:]}
            logging.info("\n\n➡️➡️➡️ TRIPLE RIGHT DETECTED! ➡️➡️➡️")
            logging.info("   → STRONG RIGHT COMMAND\n")
            movement_history.clear()
            return
    
    if len(sequence) >= 2:
        # Left-right or right-left
        if sequence[-2:] == ['left', 'right']:
            pattern_detected_flag = {'type': 'left_right', 'sequence': sequence[-2:]}
            logging.info("\n\n⬅️➡️ LEFT-RIGHT SEQUENCE DETECTED! ⬅️➡️")
            logging.info("   → SCAN/SEARCH COMMAND\n")
            movement_history.clear()
            return
        elif sequence[-2:] == ['right', 'left']:
            pattern_detected_flag = {'type': 'right_left', 'sequence': sequence[-2:]}
            logging.info("\n\n➡️⬅️ RIGHT-LEFT SEQUENCE DETECTED! ➡️⬅️")
            logging.info("   → SCAN/SEARCH COMMAND\n")
            movement_history.clear()
            return
    
    # Single movement
    if len(sequence) == 1:
        direction = sequence[0]
        if direction == 'left':
            pattern_detected_flag = {'type': 'single_left', 'sequence': [direction]}
            logging.info("\n\n⬅️ SINGLE LEFT MOVEMENT DETECTED! ⬅️")
            logging.info("   → LEFT NAVIGATION COMMAND\n")
        else:
            pattern_detected_flag = {'type': 'single_right', 'sequence': [direction]}
            logging.info("\n\n➡️ SINGLE RIGHT MOVEMENT DETECTED! ➡️")
            logging.info("   → RIGHT NAVIGATION COMMAND\n")
        movement_history.clear()

# ——— FILTERING FUNCTIONS (adapted for horizontal movements) ———————

def butter_bandpass(lowcut, highcut, fs, order=4):
    """Bandpass filter optimized for horizontal movements"""
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
    """Apply Savitzky-Golay smoothing optimized for movement tracking"""
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

def apply_movement_filter(data, fs):
    """Enhanced filtering pipeline optimized for horizontal eye movements"""
    if len(data) < 20:
        return data
    
    # Step 1: Light median filter to remove impulse noise
    data_median = medfilt(data, kernel_size=3)
    
    # Check if we have enough data for bandpass filtering
    if len(data_median) < MIN_LENGTH_FOR_FILTER:
        return data_median
    
    try:
        # Step 2: Bandpass filter optimized for horizontal movements
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
        
        # Step 4: Savitzky-Golay smoothing for movement tracking
        data_smoothed = apply_savgol_smoothing(data_notched)
        
        return data_smoothed
        
    except ValueError as e:
        if "padlen" in str(e):
            logging.warning(f"Filter failed due to short signal length ({len(data)} samples), using median filter only")
            return data_median
        else:
            logging.error(f"Unexpected error in filtering: {e}")
            return data_median

def calculate_adaptive_baseline(data, window_size=int(FS * 1.0)):
    """Calculate adaptive baseline for horizontal movement detection"""
    if len(data) < 20:
        return 0.0
    
    # Use sliding window median for robust baseline
    recent_data = np.array(data[-window_size:])
    return np.median(recent_data)

def detect_horizontal_movements(filtered_signal, baseline, fs):
    """Detect horizontal eye movements using differential analysis"""
    global last_detection_time, movement_detected_flag, current_direction, direction_start_time, last_baseline
    
    current_time = time.time()
    
    # Cooldown check
    if current_time - last_detection_time < DETECTION_COOLDOWN:
        return False
    
    if len(filtered_signal) < MOVEMENT_MIN_DURATION * 2:
        return False
    
    # Baseline-corrected signal
    corrected_signal = filtered_signal - baseline
    current_value = corrected_signal[-1]
    
    # Calculate derivative for movement detection
    if len(corrected_signal) >= 10:
        derivative = np.gradient(corrected_signal[-10:])
        movement_velocity = abs(derivative[-1])
    else:
        movement_velocity = 0
    
    # Determine movement direction and strength
    movement_detected = False
    direction = None
    amplitude = abs(current_value)
    
    # Strong movement detection
    if amplitude > MOVEMENT_THRESH_HIGH:
        if current_value > MOVEMENT_THRESH_HIGH:
            direction = 'right'  # Positive values = right movement
        elif current_value < -MOVEMENT_THRESH_HIGH:
            direction = 'left'   # Negative values = left movement
        movement_detected = True
        
    # Subtle movement detection (with higher validation requirements)
    elif amplitude > MOVEMENT_THRESH_LOW:
        # Require sustained movement for subtle detections
        validation_samples = int(FS * 0.15)  # 150ms validation
        recent_signal = corrected_signal[-validation_samples:]
        
        if len(recent_signal) >= validation_samples:
            # Check for consistent direction
            if current_value > MOVEMENT_THRESH_LOW:
                consistent_samples = np.sum(recent_signal > MOVEMENT_THRESH_LOW * 0.7)
                if consistent_samples >= validation_samples * VALIDATION_STRICTNESS:
                    direction = 'right'
                    movement_detected = True
            elif current_value < -MOVEMENT_THRESH_LOW:
                consistent_samples = np.sum(recent_signal < -MOVEMENT_THRESH_LOW * 0.7)
                if consistent_samples >= validation_samples * VALIDATION_STRICTNESS:
                    direction = 'left'
                    movement_detected = True
    
    # Apply direction hysteresis to avoid rapid switching
    if direction and current_direction and direction != current_direction:
        if current_time - direction_start_time < 0.2:  # 200ms hysteresis
            direction = current_direction  # Keep previous direction
    
    if movement_detected and direction:
        # Additional validation: check for movement characteristics
        if len(corrected_signal) >= 20:
            recent_20 = corrected_signal[-20:]
            signal_range = np.max(recent_20) - np.min(recent_20)
            mean_abs = np.mean(np.abs(recent_20))
            
            # Movement should have reasonable range and consistent amplitude
            if signal_range > MOVEMENT_THRESH_LOW and mean_abs > MOVEMENT_THRESH_LOW * 0.3:
                
                # Update direction tracking
                if direction != current_direction:
                    current_direction = direction
                    direction_start_time = current_time
                
                last_detection_time = current_time
                movement_detected_flag = {
                    'detected': True,
                    'direction': direction,
                    'amplitude': amplitude
                }
                
                logging.info(f"👁️  HORIZONTAL MOVEMENT DETECTED!")
                logging.info(f"   Direction: {direction.upper()}")
                logging.info(f"   Amplitude: {amplitude:.2f}mV")
                logging.info(f"   Baseline: {baseline:.2f}mV")
                logging.info(f"   Signal range: {signal_range:.2f}mV")
                
                # Add to pattern tracking
                add_movement_to_history(current_time, direction, amplitude)
                
                return True
            else:
                logging.debug(f"❌ Movement rejected: range={signal_range:.2f}, mean_abs={mean_abs:.2f}")
    
    return False

# ——— ENHANCED PLOTTING SETUP ———————————————————————————————————

class HorizontalMovementPlotter:
    def __init__(self):
        plt.style.use('default')
        self.fig, ((self.ax1, self.ax2), (self.ax3, self.ax4)) = plt.subplots(2, 2, figsize=(18, 12))
        self.fig.suptitle('Horizontal EOG Movement Detection: ⬅️ Left | ➡️ Right | 🔄 Patterns', fontsize=16)
        
        # Plot 1: Raw vs Filtered
        self.line_raw, = self.ax1.plot([], [], 'b-', alpha=0.6, label='Raw Signal', linewidth=1)
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
        
        # Plot 3: Movement detection with thresholds
        self.line_corrected, = self.ax3.plot([], [], 'purple', label='Baseline Corrected', linewidth=2)
        self.line_thresh_high_pos, = self.ax3.plot([], [], 'r-', label=f'High Threshold (±{MOVEMENT_THRESH_HIGH}mV)', linewidth=2)
        self.line_thresh_high_neg, = self.ax3.plot([], [], 'r-', linewidth=2)
        self.line_thresh_low_pos, = self.ax3.plot([], [], 'orange', alpha=0.7, label=f'Low Threshold (±{MOVEMENT_THRESH_LOW}mV)', linewidth=1)
        self.line_thresh_low_neg, = self.ax3.plot([], [], 'orange', alpha=0.7, linewidth=1)
        self.ax3.set_ylabel('Amplitude (mV)')
        self.ax3.set_xlabel('Time (seconds)')
        self.ax3.set_title('Horizontal Movement Detection')
        self.ax3.legend()
        self.ax3.grid(True, alpha=0.3)
        
        # Plot 4: Movement Status and History
        self.ax4.clear()
        self.ax4.set_xlim(0, 10)
        self.ax4.set_ylim(0, 6)
        self.ax4.set_title('Movement Commands & Patterns')
        self.ax4.grid(True, alpha=0.3)
        
        # Status displays
        self.command_text = self.ax4.text(5, 4.5, 'Ready for movements...', 
                                         ha='center', va='center', fontsize=12, 
                                         bbox=dict(boxstyle="round,pad=0.3", facecolor="lightblue"))
        
        self.movement_text = self.ax4.text(5, 3, 'Last Movement: None', 
                                          ha='center', va='center', fontsize=10)
        
        self.history_text = self.ax4.text(5, 2, 'Recent movements: 0', 
                                         ha='center', va='center', fontsize=10)
        
        self.pattern_text = self.ax4.text(5, 1, 'Pattern: Waiting...', 
                                         ha='center', va='center', fontsize=10)
        
        # Movement markers for other plots
        self.movement_markers = []
        
    def update_plot(self, frame):
        global movement_detected_flag, pattern_detected_flag, movement_history
        
        with data_lock:
            if len(timestamps_plot) < 10:
                return tuple()
            
            # Convert timestamps to relative seconds
            times = np.array(list(timestamps_plot))
            raw_data = np.array(list(plot_buffer_raw))
            filtered_data = np.array(list(plot_buffer_filtered))
            smoothed_data = np.array(list(plot_buffer_smoothed))
            baseline_data = np.array(list(plot_buffer_baseline))
            
            # Check for movement detection
            if movement_detected_flag['detected']:
                self.mark_movement(movement_detected_flag['direction'], movement_detected_flag['amplitude'])
                self.movement_text.set_text(f"Last Movement: {movement_detected_flag['direction'].upper()} ({movement_detected_flag['amplitude']:.1f}mV)")
                movement_detected_flag = {'detected': False, 'direction': None, 'amplitude': 0}
            
            # Check for pattern detection
            if pattern_detected_flag['type']:
                self.show_pattern(pattern_detected_flag['type'], pattern_detected_flag['sequence'])
                pattern_detected_flag = {'type': None, 'sequence': []}
            
            # Update movement history display
            current_time = time.time()
            recent_movements = [m for m in movement_history if current_time - m['time'] <= SEQUENCE_TIMEOUT]
            self.history_text.set_text(f'Recent movements: {len(recent_movements)}')
            
            # Show recent sequence
            if recent_movements:
                sequence_str = " → ".join([m['direction'][0].upper() for m in recent_movements[-5:]])
                self.pattern_text.set_text(f'Sequence: {sequence_str}')
            else:
                self.pattern_text.set_text('Pattern: Waiting...')
        
        if len(times) > 0:
            times = times - times[-1]  # Relative to current time
        
        # Update signal processing plots
        self.line_raw.set_data(times, raw_data)
        self.line_filtered.set_data(times, filtered_data)
        self.line_smoothed.set_data(times, smoothed_data)
        
        # Update baseline tracking
        self.line_signal.set_data(times, filtered_data)
        self.line_baseline.set_data(times, baseline_data)
        
        # Update movement detection plot
        corrected_data = filtered_data - baseline_data
        self.line_corrected.set_data(times, corrected_data)
        
        # Update threshold lines
        if len(times) > 0:
            thresh_high_pos = np.full_like(times, MOVEMENT_THRESH_HIGH)
            thresh_high_neg = np.full_like(times, -MOVEMENT_THRESH_HIGH)
            thresh_low_pos = np.full_like(times, MOVEMENT_THRESH_LOW)
            thresh_low_neg = np.full_like(times, -MOVEMENT_THRESH_LOW)
            
            self.line_thresh_high_pos.set_data(times, thresh_high_pos)
            self.line_thresh_high_neg.set_data(times, thresh_high_neg)
            self.line_thresh_low_pos.set_data(times, thresh_low_pos)
            self.line_thresh_low_neg.set_data(times, thresh_low_neg)
        
        # Auto-scale axes (first 3 plots)
        for ax in [self.ax1, self.ax2, self.ax3]:
            ax.relim()
            ax.autoscale_view()
            if len(times) > 0:
                ax.set_xlim(-PLOT_WINDOW_SEC, 0)
        
        return tuple()
    
    def mark_movement(self, direction, amplitude):
        """Add a movement detection marker"""
        current_time = 0  # Will be at the right edge
        color = 'green' if direction == 'right' else 'red'
        
        for ax in [self.ax1, self.ax2, self.ax3]:
            marker = ax.axvline(current_time, color=color, linestyle='-', linewidth=3, alpha=0.8)
            self.movement_markers.append(marker)
        
        # Remove old markers (keep only last 20)
        if len(self.movement_markers) > 40:
            for marker in self.movement_markers[:20]:
                marker.remove()
            self.movement_markers = self.movement_markers[20:]
    
    def show_pattern(self, pattern_type, sequence):
        """Show detected movement pattern"""
        patterns = {
            'single_left': {'text': '⬅️ SINGLE LEFT\nNAVIGATE LEFT', 'color': 'lightcoral'},
            'single_right': {'text': '➡️ SINGLE RIGHT\nNAVIGATE RIGHT', 'color': 'lightgreen'},
            'left_right': {'text': '⬅️➡️ LEFT-RIGHT\nSCAN COMMAND', 'color': 'lightyellow'},
            'right_left': {'text': '➡️⬅️ RIGHT-LEFT\nSCAN COMMAND', 'color': 'lightyellow'},
            'triple_left': {'text': '⬅️⬅️⬅️ TRIPLE LEFT\nSTRONG LEFT', 'color': 'lightcoral'},
            'triple_right': {'text': '➡️➡️➡️ TRIPLE RIGHT\nSTRONG RIGHT', 'color': 'lightgreen'},
            'alternating_lr': {'text': '🔄 L-R-L-R\nCOMPLEX NAV', 'color': 'lightblue'},
            'alternating_rl': {'text': '🔄 R-L-R-L\nCOMPLEX NAV', 'color': 'lightblue'}
        }
        
        pattern_info = patterns.get(pattern_type, {'text': 'UNKNOWN', 'color': 'lightgray'})
        
        self.command_text.set_text(pattern_info['text'])
        self.command_text.set_bbox(dict(boxstyle="round,pad=0.5", facecolor=pattern_info['color']))
        
        # Clear after 4 seconds
        threading.Timer(4.0, self.clear_command_display).start()
    
    def clear_command_display(self):
        """Clear command display"""
        self.command_text.set_text('Ready for movements...')
        self.command_text.set_bbox(dict(boxstyle="round,pad=0.3", facecolor="lightblue"))

# ——— NOTIFICATION HANDLER ————————————————————————————————————————

def notification_handler(sender, data: bytearray):
    """Enhanced notification handler for horizontal movement detection"""
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
                plot_buffer_filtered.append(eog_raw_mv)
                plot_buffer_smoothed.append(eog_raw_mv)
                plot_buffer_baseline.append(0)
                return

            # Apply filtering
            filtered_signal = apply_movement_filter(list(signal_buffer), FS)
            baseline = calculate_adaptive_baseline(list(baseline_buffer))
            smoothed_signal = apply_savgol_smoothing(filtered_signal)

            # Store processed data
            plot_buffer_filtered.append(filtered_signal[-1])
            plot_buffer_smoothed.append(smoothed_signal[-1])
            plot_buffer_baseline.append(baseline)

            # Detect movements
            detect_horizontal_movements(filtered_signal, baseline, FS)

    except Exception as e:
        logging.error(f"Error in notification_handler: {e}")


# ——— MAIN EVENT LOOP ————————————————————————————————————————
async def main():
    global ble_client, ble_connected

    plotter = HorizontalMovementPlotter()
    ani = animation.FuncAnimation(plotter.fig, plotter.update_plot,
                                  interval=PLOT_UPDATE_MS, blit=False)

    async with BleakClient(ADDRESS) as client:
        ble_client = client
        ble_connected = await client.is_connected()
        if not ble_connected:
            logging.error("Failed to connect to SensorTile.")
            return
        logging.info("Connected to SensorTile.")

        await client.start_notify(CHAR_UUID_NOTIFY, notification_handler)

        try:
            plt.show()
        finally:
            await client.stop_notify(CHAR_UUID_NOTIFY)


if __name__ == "__main__":
    asyncio.run(main())
