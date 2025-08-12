#!/usr/bin/env python3
"""
Horizontal EOG Eye Movement Detection
- Detects left/right eye movements
- Real-time plotting with movement direction indicators
- CSV logging for movement analysis
- Optimized for horizontal electrode placement
"""

import asyncio
import struct
import logging
import csv
import os
from collections import deque
import time
import threading
from datetime import datetime

import numpy as np
import matplotlib.pyplot as plt
import matplotlib.animation as animation
from scipy.signal import butter, filtfilt, iirnotch, medfilt, savgol_filter
from bleak import BleakClient
from mac import ADDRESS  # your SensorTile BLE address

# ——— VISUAL CONFIG ——————————————————————————————————————————————
PLOT_WINDOW_SEC = 8.0         # Show last 8 seconds of data
PLOT_UPDATE_MS = 50           # Update plot every 50ms (more responsive)

# ——— SIGNAL CONFIG ——————————————————————————————————————————————
FS = 240.0                    # sampling rate (Hz)
VAFE_GAIN_LSB_PER_MV = 78     # From datasheet: 78 LSB/mV

# Filtering parameters optimized for eye movements
NOTCH_F = 60.0               # mains notch freq (Hz)
NOTCH_Q = 30.0               # notch Q
EOG_LOWPASS = 25.0           # Lower cutoff to preserve eye movement components
EOG_HIGHPASS = 0.05          # Very low to preserve slow eye movements
FILTER_ORDER = 4

# Buffer sizes
BUFFER_SEC = 1.0             # Processing buffer (1 second)
BUFFER_SIZE = int(FS * BUFFER_SEC)
PLOT_BUFFER_SIZE = int(FS * PLOT_WINDOW_SEC)

# EYE MOVEMENT DETECTION PARAMETERS
# Threshold for detecting significant eye movements (start lower, adjust based on your signal)
MOVEMENT_THRESH = 2.0        # 2mV threshold for eye movement detection
LEFT_THRESH = -MOVEMENT_THRESH   # Negative deflection = look left (typically)
RIGHT_THRESH = MOVEMENT_THRESH   # Positive deflection = look right (typically)

# Movement validation parameters
MIN_MOVEMENT_DURATION = int(FS * 0.1)   # 100ms minimum duration
MAX_MOVEMENT_DURATION = int(FS * 2.0)   # 2 second maximum duration
BASELINE_WINDOW = int(FS * 3.0)         # 3 second baseline calculation

# Detection parameters
MOVEMENT_COOLDOWN = 0.3      # 300ms between movements
MOVEMENT_HYSTERESIS = 0.6    # 60% of threshold to end movement
SMOOTHING_WINDOW = 7         # Smaller window for responsive movement detection

# Minimum signal lengths for filtering
MIN_LENGTH_FOR_FILTER = 50
MIN_LENGTH_FOR_NOTCH = 25

# BLE UUIDs
CHAR_UUID_NOTIFY = "00000001-0004-11e1-ac36-0002a5d5c51b"
CHAR_UUID_WRITE = "00000002-0004-11e1-ac36-0002a5d5c51b"

# Setup logging to both console and CSV file
csv_filename = f"horizontal_eog_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(f"horizontal_eog_console_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")
    ]
)

# CSV logging setup
csv_lock = threading.Lock()
csv_file = None
csv_writer = None

def init_csv_logging():
    """Initialize CSV file for eye movement logging"""
    global csv_file, csv_writer
    
    try:
        csv_file = open(csv_filename, 'w', newline='', encoding='utf-8')
        csv_writer = csv.writer(csv_file)
        
        # Write CSV header
        csv_writer.writerow([
            'timestamp',
            'datetime',
            'raw_mv',
            'filtered_mv', 
            'baseline_mv',
            'corrected_mv',
            'movement_detected',
            'movement_direction',
            'movement_amplitude',
            'movement_duration_ms'
        ])
        csv_file.flush()
        logging.info(f"📊 CSV logging initialized: {csv_filename}")
        
    except Exception as e:
        logging.error(f"Failed to initialize CSV logging: {e}")

def log_to_csv(timestamp, raw_mv, filtered_mv, baseline_mv, corrected_mv, 
               movement_detected=False, movement_direction="", movement_amplitude=None, 
               movement_duration_ms=None):
    """Log eye movement data to CSV file"""
    global csv_writer, csv_file
    
    if csv_writer is None:
        return
        
    try:
        with csv_lock:
            csv_writer.writerow([
                f"{timestamp:.6f}",
                datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3],
                f"{raw_mv:.3f}",
                f"{filtered_mv:.3f}",
                f"{baseline_mv:.3f}",
                f"{corrected_mv:.3f}",
                movement_detected,
                movement_direction,
                f"{movement_amplitude:.3f}" if movement_amplitude is not None else "",
                movement_duration_ms if movement_duration_ms is not None else ""
            ])
            csv_file.flush()
            
    except Exception as e:
        logging.error(f"Error writing to CSV: {e}")

def close_csv_logging():
    """Close CSV file properly"""
    global csv_file
    if csv_file:
        try:
            csv_file.close()
            logging.info(f"📊 CSV logging closed: {csv_filename}")
        except:
            pass

# Data buffers (thread-safe)
signal_buffer = deque(maxlen=BUFFER_SIZE)
plot_buffer_raw = deque(maxlen=PLOT_BUFFER_SIZE)
plot_buffer_filtered = deque(maxlen=PLOT_BUFFER_SIZE)
plot_buffer_baseline = deque(maxlen=PLOT_BUFFER_SIZE)
timestamps_plot = deque(maxlen=PLOT_BUFFER_SIZE)

# Movement detection state
last_movement_time = 0
baseline_buffer = deque(maxlen=BASELINE_WINDOW)
movement_state = {
    'in_movement': False, 
    'movement_start': 0, 
    'movement_samples': 0,
    'movement_direction': 'none',
    'peak_amplitude': 0
}

# Threading control
data_lock = threading.Lock()
ble_client = None
ble_connected = False
movement_detected_flag = {'detected': False, 'direction': 'none', 'amplitude': 0}

# ——— FILTERING FUNCTIONS ———————————————————————————————————————

def butter_bandpass(lowcut, highcut, fs, order=4):
    """Bandpass filter optimized for eye movements"""
    nyq = 0.5 * fs
    low = lowcut / nyq
    high = highcut / nyq
    b, a = butter(order, [low, high], btype='band')
    return b, a

def apply_notch(data, fs, f0=NOTCH_F, Q=NOTCH_Q):
    """60Hz notch filter"""
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
    except ValueError:
        return data

def apply_horizontal_eog_filter(data, fs):
    """EOG filtering pipeline optimized for horizontal eye movements"""
    if len(data) < 20:
        return data
    
    # Step 1: Light median filter for spike removal
    data_median = medfilt(data, kernel_size=3)
    
    if len(data_median) < MIN_LENGTH_FOR_FILTER:
        return data_median
    
    try:
        # Step 2: Bandpass filter (preserve slow eye movements)
        b, a = butter_bandpass(EOG_HIGHPASS, EOG_LOWPASS, fs, order=FILTER_ORDER)
        
        required_length = 3 * max(len(a), len(b))
        if len(data_median) <= required_length:
            b, a = butter_bandpass(EOG_HIGHPASS, EOG_LOWPASS, fs, order=2)
            required_length = 3 * max(len(a), len(b))
            if len(data_median) <= required_length:
                return data_median
        
        data_bandpass = filtfilt(b, a, data_median)
        
        # Step 3: 60Hz notch filter
        data_notched = apply_notch(data_bandpass, fs)
        
        # Step 4: Light smoothing (smaller window for responsiveness)
        if len(data_notched) >= SMOOTHING_WINDOW:
            window = SMOOTHING_WINDOW if SMOOTHING_WINDOW % 2 == 1 else SMOOTHING_WINDOW + 1
            data_smoothed = savgol_filter(data_notched, window, 2)
        else:
            data_smoothed = data_notched
        
        return data_smoothed
        
    except ValueError as e:
        logging.warning(f"Filtering failed, using median filter: {e}")
        return data_median

def calculate_baseline(data):
    """Calculate baseline for horizontal movements"""
    if len(data) < 20:
        return 0.0
    
    # Use median of recent data (more stable for eye movements)
    recent_data = np.array(data[-BASELINE_WINDOW//2:])
    return np.median(recent_data)

def detect_horizontal_movements(filtered_signal, baseline, fs):
    """Detect left/right eye movements"""
    global last_movement_time, movement_detected_flag, movement_state
    
    current_time = time.time()
    
    # Cooldown check
    if current_time - last_movement_time < MOVEMENT_COOLDOWN:
        return False
    
    if len(filtered_signal) < MIN_MOVEMENT_DURATION:
        return False
    
    # Baseline-corrected signal
    corrected_signal = filtered_signal - baseline
    current_amplitude = corrected_signal[-1]  # Keep sign for direction
    
    # Check for movement onset
    if not movement_state['in_movement']:
        # Look for significant deflection in either direction
        if current_amplitude > RIGHT_THRESH:
            # Right movement detected
            movement_state['in_movement'] = True
            movement_state['movement_start'] = len(corrected_signal) - 1
            movement_state['movement_samples'] = 1
            movement_state['movement_direction'] = 'RIGHT'
            movement_state['peak_amplitude'] = current_amplitude
            
            logging.info(f"👀 RIGHT MOVEMENT START: {current_amplitude:.2f}mV > {RIGHT_THRESH:.2f}mV")
            
        elif current_amplitude < LEFT_THRESH:
            # Left movement detected
            movement_state['in_movement'] = True
            movement_state['movement_start'] = len(corrected_signal) - 1
            movement_state['movement_samples'] = 1
            movement_state['movement_direction'] = 'LEFT'
            movement_state['peak_amplitude'] = abs(current_amplitude)
            
            logging.info(f"👀 LEFT MOVEMENT START: {current_amplitude:.2f}mV < {LEFT_THRESH:.2f}mV")
            
    else:
        # In movement, check for continuation or end
        movement_state['movement_samples'] += 1
        
        # Update peak amplitude
        if movement_state['movement_direction'] == 'RIGHT':
            if current_amplitude > movement_state['peak_amplitude']:
                movement_state['peak_amplitude'] = current_amplitude
        else:  # LEFT
            if abs(current_amplitude) > movement_state['peak_amplitude']:
                movement_state['peak_amplitude'] = abs(current_amplitude)
        
        # Check for movement end (using hysteresis)
        movement_ended = False
        if movement_state['movement_direction'] == 'RIGHT':
            if current_amplitude < RIGHT_THRESH * MOVEMENT_HYSTERESIS:
                movement_ended = True
        else:  # LEFT
            if current_amplitude > LEFT_THRESH * MOVEMENT_HYSTERESIS:
                movement_ended = True
        
        if movement_ended or movement_state['movement_samples'] > MAX_MOVEMENT_DURATION:
            # Movement ended - validate it
            duration_ms = (movement_state['movement_samples'] / fs) * 1000
            
            if MIN_MOVEMENT_DURATION <= movement_state['movement_samples'] <= MAX_MOVEMENT_DURATION:
                # Valid movement detected
                last_movement_time = current_time
                movement_detected_flag = {
                    'detected': True,
                    'direction': movement_state['movement_direction'],
                    'amplitude': movement_state['peak_amplitude']
                }
                
                logging.info(f"✅ {movement_state['movement_direction']} MOVEMENT DETECTED!")
                logging.info(f"   Peak amplitude: {movement_state['peak_amplitude']:.2f}mV")
                logging.info(f"   Duration: {duration_ms:.0f}ms")
                logging.info(f"   Samples: {movement_state['movement_samples']}")
                
                # Log to CSV
                log_to_csv(
                    current_time,
                    filtered_signal[-1] + baseline,  # Reconstruct raw-ish value
                    filtered_signal[-1],
                    baseline,
                    current_amplitude,
                    movement_detected=True,
                    movement_direction=movement_state['movement_direction'],
                    movement_amplitude=movement_state['peak_amplitude'],
                    movement_duration_ms=int(duration_ms)
                )
                
                # Reset movement state
                movement_state = {
                    'in_movement': False, 
                    'movement_start': 0, 
                    'movement_samples': 0,
                    'movement_direction': 'none',
                    'peak_amplitude': 0
                }
                
                return True
            else:
                logging.debug(f"❌ Movement rejected: duration {duration_ms:.0f}ms not in valid range")
            
            # Reset movement state
            movement_state = {
                'in_movement': False, 
                'movement_start': 0, 
                'movement_samples': 0,
                'movement_direction': 'none',
                'peak_amplitude': 0
            }
    
    return False

# ——— PLOTTING SETUP ———————————————————————————————————————————

class HorizontalEOGPlotter:
    def __init__(self):
        plt.style.use('default')
        self.fig, (self.ax1, self.ax2) = plt.subplots(2, 1, figsize=(14, 8))
        self.fig.suptitle('Horizontal EOG Eye Movement Detection', fontsize=14)
        
        # Plot 1: Signal processing
        self.line_raw, = self.ax1.plot([], [], 'b-', alpha=0.7, label='Raw Signal', linewidth=1)
        self.line_filtered, = self.ax1.plot([], [], 'g-', label='Filtered Signal', linewidth=2)
        self.line_baseline, = self.ax1.plot([], [], 'r--', label='Baseline', linewidth=1)
        self.ax1.set_ylabel('Amplitude (mV)')
        self.ax1.set_title('Signal Processing')
        self.ax1.legend()
        self.ax1.grid(True, alpha=0.3)
        
        # Plot 2: Movement detection with thresholds
        self.line_corrected, = self.ax2.plot([], [], 'purple', label='Baseline Corrected', linewidth=2)
        self.line_right_thresh, = self.ax2.plot([], [], 'r--', label=f'Right Threshold (+{MOVEMENT_THRESH}mV)', linewidth=2)
        self.line_left_thresh, = self.ax2.plot([], [], 'b--', label=f'Left Threshold ({LEFT_THRESH}mV)', linewidth=2)
        self.line_zero, = self.ax2.plot([], [], 'k-', alpha=0.3, linewidth=1)
        self.ax2.set_ylabel('Amplitude (mV)')
        self.ax2.set_xlabel('Time (seconds)')
        self.ax2.set_title('Eye Movement Detection')
        self.ax2.legend()
        self.ax2.grid(True, alpha=0.3)
        
        # Movement markers
        self.movement_markers = []
        
    def update_plot(self, frame):
        global movement_detected_flag
        
        with data_lock:
            if len(timestamps_plot) < 10:
                return self.line_raw, self.line_filtered, self.line_baseline, self.line_corrected
            
            # Convert timestamps to relative seconds
            times = np.array(list(timestamps_plot))
            raw_data = np.array(list(plot_buffer_raw))
            filtered_data = np.array(list(plot_buffer_filtered))
            baseline_data = np.array(list(plot_buffer_baseline))
            
            # Check for movement detection
            if movement_detected_flag['detected']:
                self.mark_movement(movement_detected_flag['direction'], movement_detected_flag['amplitude'])
                movement_detected_flag = {'detected': False, 'direction': 'none', 'amplitude': 0}
        
        if len(times) > 0:
            times = times - times[-1]  # Relative to current time
        
        # Update signal processing plots
        self.line_raw.set_data(times, raw_data)
        self.line_filtered.set_data(times, filtered_data)
        self.line_baseline.set_data(times, baseline_data)
        
        # Update movement detection plot
        corrected_data = filtered_data - baseline_data
        self.line_corrected.set_data(times, corrected_data)
        
        # Update threshold lines
        if len(times) > 0:
            right_thresh = np.full_like(times, RIGHT_THRESH)
            left_thresh = np.full_like(times, LEFT_THRESH)
            zero_line = np.full_like(times, 0)
            self.line_right_thresh.set_data(times, right_thresh)
            self.line_left_thresh.set_data(times, left_thresh)
            self.line_zero.set_data(times, zero_line)
        
        # Auto-scale axes
        for ax in [self.ax1, self.ax2]:
            ax.relim()
            ax.autoscale_view()
            if len(times) > 0:
                ax.set_xlim(-PLOT_WINDOW_SEC, 0)
        
        return self.line_raw, self.line_filtered, self.line_baseline, self.line_corrected
    
    def mark_movement(self, direction, amplitude):
        """Add a movement detection marker"""
        current_time = 0  # Will be at the right edge
        
        # Choose color based on direction
        color = 'red' if direction == 'RIGHT' else 'blue'
        
        for ax in [self.ax1, self.ax2]:
            marker = ax.axvline(current_time, color=color, linestyle='-', linewidth=3, alpha=0.8)
            self.movement_markers.append(marker)
        
        # Add direction and amplitude text
        self.ax2.text(current_time, amplitude if direction == 'RIGHT' else -amplitude, 
                     f'{direction}\n{amplitude:.1f}mV', 
                     color=color, fontsize=10, ha='center', fontweight='bold')
        
        # Remove old markers (keep only last 10)
        if len(self.movement_markers) > 20:
            for marker in self.movement_markers[:10]:
                marker.remove()
            self.movement_markers = self.movement_markers[10:]

# ——— NOTIFICATION HANDLER ———————————————————————————————————————

def notification_handler(sender, data: bytearray):
    """Notification handler for horizontal eye movement detection"""
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
            
            # Only process when we have enough data
            if len(signal_buffer) < MIN_LENGTH_FOR_FILTER:
                plot_buffer_filtered.append(eog_raw_mv)
                plot_buffer_baseline.append(0.0)
                return
            
            # Apply horizontal EOG filtering
            signal_array = np.array(list(signal_buffer))
            filtered_signal = apply_horizontal_eog_filter(signal_array, FS)
            
            # Calculate baseline
            baseline = calculate_baseline(list(baseline_buffer))
            
            # Store for plotting
            plot_buffer_filtered.append(filtered_signal[-1])
            plot_buffer_baseline.append(baseline)
            
            # Eye movement detection
            movement_detected = detect_horizontal_movements(filtered_signal, baseline, FS)
            
            # Regular logging (every 40th sample)
            if len(signal_buffer) % 40 == 0:
                corrected_amplitude = filtered_signal[-1] - baseline
                
                logging.info(
                    f"Horizontal EOG: raw={eog_raw_mv:.2f}mV | filtered={filtered_signal[-1]:.2f}mV | "
                    f"baseline={baseline:.2f}mV | corrected={corrected_amplitude:.2f}mV | "
                    f"R_thresh={RIGHT_THRESH:.1f} | L_thresh={LEFT_THRESH:.1f}"
                )
                
                # Log regular data to CSV (every 10th sample for manageable file size)
                if len(signal_buffer) % 10 == 0:
                    log_to_csv(
                        current_time,
                        eog_raw_mv,
                        filtered_signal[-1],
                        baseline,
                        corrected_amplitude,
                        movement_detected=False
                    )
                    
    except Exception as e:
        logging.error(f"Error in notification handler: {e}")

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
        logging.info("📡 Streaming EOG for horizontal eye movement detection.")

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
    """Main function for horizontal EOG detection"""
    logging.info("🚀 Starting Horizontal EOG Eye Movement Detection")
    logging.info(f"📊 Sampling Rate: {FS} Hz")
    logging.info(f"🔧 Bandpass Filter: {EOG_HIGHPASS}-{EOG_LOWPASS} Hz")
    logging.info(f"⚡ Notch Filter: {NOTCH_F} Hz")
    logging.info(f"👀 Movement Thresholds: LEFT={LEFT_THRESH}mV, RIGHT={RIGHT_THRESH}mV")
    logging.info(f"⏰ Movement Cooldown: {MOVEMENT_COOLDOWN} seconds")
    logging.info(f"📏 Duration Range: {MIN_MOVEMENT_DURATION}-{MAX_MOVEMENT_DURATION} samples")
    logging.info("")
    logging.info("🎯 ELECTRODE PLACEMENT for Horizontal EOG:")
    logging.info("   • Place one electrode on LEFT temple (near outer corner of left eye)")
    logging.info("   • Place other electrode on RIGHT temple (near outer corner of right eye)")
    logging.info("   • Reference electrode on forehead or earlobe")
    logging.info("   • Look LEFT = negative signal, Look RIGHT = positive signal")
    logging.info("")
    
    # Initialize CSV logging
    init_csv_logging()
    
    try:
        # Start BLE connection
        ble_thread = threading.Thread(target=run_ble_async, daemon=True)
        ble_thread.start()
        
        time.sleep(3)
        
        # Set up plotting
        plotter = HorizontalEOGPlotter()
        
        def on_close(event):
            global ble_connected
            ble_connected = False
            logging.info("🛑 Plot window closed, stopping BLE connection...")
            close_csv_logging()
        
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
        logging.info("📊 Starting plot - Try looking left and right!")
        logging.info(f"📈 Data will be saved to: {csv_filename}")
        plt.show()
        
    except KeyboardInterrupt:
        logging.info("🛑 Interrupted by user")
    except Exception as e:
        logging.error(f"❌ Application error: {e}")
    finally:
        global ble_connected
        ble_connected = False
        close_csv_logging()
        logging.info("✅ Application closed.")
        logging.info(f"📊 Final CSV file: {csv_filename}")

if __name__ == '__main__':
    main()