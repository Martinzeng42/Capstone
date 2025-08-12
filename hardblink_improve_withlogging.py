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
import csv
import os
from collections import deque
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

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

# HARD BLINK THRESHOLDS - back to reliable detection
HARD_BLINK_THRESH = 6.0      # 6mV threshold for hard blinks (reduced from 8mV but still high)
BLINK_MIN_SAMPLES = int(FS * 0.05)   # 50ms minimum duration
BLINK_MAX_SAMPLES = int(FS * 0.6)    # 600ms maximum duration
BASELINE_WINDOW = int(FS * 2.5)      # 2.5 second baseline calculation

# Detection parameters - tuned for hard blinks
DETECTION_COOLDOWN = 0.8     # 800ms cooldown to prevent double detection
MIN_PEAK_RATIO = 1.5         # Peak must be 50% higher than surrounding signal
VALIDATION_STRICTNESS = 0.7  # 70% of blink must be above threshold * 0.8

# Smoothing parameters (keep the noise reduction features)
SAVGOL_WINDOW = 15           # Savitzky-Golay smoothing window
SAVGOL_POLYORDER = 3         # Polynomial order for smoothing

# Minimum signal lengths for filtering
MIN_LENGTH_FOR_FILTER = 50   # Reduced from 60
MIN_LENGTH_FOR_NOTCH = 25    # Reduced from 30

# BLE UUIDs
CHAR_UUID_NOTIFY = "00000001-0004-11e1-ac36-0002a5d5c51b"
CHAR_UUID_WRITE = "00000002-0004-11e1-ac36-0002a5d5c51b"

# Setup logging to both console and CSV file
log_filename = f"eog_blink_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
csv_filename = f"eog_data_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),  # Console output
        logging.FileHandler(f"eog_console_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")  # Text log file
    ]
)

# CSV logging setup
csv_lock = threading.Lock()
csv_file = None
csv_writer = None

def init_csv_logging():
    """Initialize CSV file for data logging"""
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
            'threshold_mv',
            'blink_detected',
            'peak_amplitude',
            'peak_ratio',
            'validation_samples'
        ])
        csv_file.flush()
        logging.info(f"📊 CSV logging initialized: {csv_filename}")
        
    except Exception as e:
        logging.error(f"Failed to initialize CSV logging: {e}")

def log_to_csv(timestamp, raw_mv, filtered_mv, baseline_mv, corrected_mv, 
               threshold_mv, blink_detected=False, peak_amplitude=None, 
               peak_ratio=None, validation_samples=None):
    """Log data to CSV file"""
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
                f"{threshold_mv:.3f}",
                blink_detected,
                f"{peak_amplitude:.3f}" if peak_amplitude is not None else "",
                f"{peak_ratio:.3f}" if peak_ratio is not None else "",
                validation_samples if validation_samples is not None else ""
            ])
            csv_file.flush()  # Ensure data is written immediately
            
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
plot_buffer_smoothed = deque(maxlen=PLOT_BUFFER_SIZE)
timestamps_plot = deque(maxlen=PLOT_BUFFER_SIZE)

# Enhanced detection state - simplified for hard blinks
last_detection_time = 0
baseline_buffer = deque(maxlen=BASELINE_WINDOW)

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

def calculate_robust_baseline(data):
    """Calculate robust baseline for hard blink detection"""
    if len(data) < 20:
        return 0.0
    
    # Use recent data but remove extreme outliers
    recent_data = np.array(data[-BASELINE_WINDOW//2:])
    
    # Simple percentile-based baseline (more stable than median with outliers)
    return np.percentile(recent_data, 50)  # 50th percentile (median)

def detect_hard_blinks_only(filtered_signal, baseline, fs):
    """Reliable hard blink detection with strict validation and CSV logging"""
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
            peak_ratio = max_recent / (mean_recent + 0.1)
            
            if peak_ratio > MIN_PEAK_RATIO:  # Clear peak
                last_detection_time = current_time
                blink_detected_flag = True
                
                # Log to both console and CSV
                logging.info(f"🔥 HARD BLINK DETECTED!")
                logging.info(f"   Current amplitude: {current_amplitude:.2f}mV")
                logging.info(f"   Peak amplitude: {max_recent:.2f}mV") 
                logging.info(f"   Threshold: {HARD_BLINK_THRESH:.2f}mV")
                logging.info(f"   High samples: {high_samples}/{validation_samples}")
                logging.info(f"   Peak ratio: {peak_ratio:.2f}")
                
                # Log detection to CSV with detailed info
                log_to_csv(
                    current_time,
                    filtered_signal[-1] + baseline,  # Reconstruct raw-ish value
                    filtered_signal[-1],
                    baseline,
                    current_amplitude,
                    HARD_BLINK_THRESH,
                    blink_detected=True,
                    peak_amplitude=max_recent,
                    peak_ratio=peak_ratio,
                    validation_samples=f"{high_samples}/{validation_samples}"
                )
                
                return True
            else:
                logging.debug(f"❌ Rejected: peak ratio {peak_ratio:.2f} < {MIN_PEAK_RATIO}")
        else:
            logging.debug(f"❌ Rejected: only {high_samples}/{validation_samples} high samples (need {required_high_samples})")
    
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
        
        # Plot 3: Detection with fixed threshold
        self.line_corrected, = self.ax3.plot([], [], 'purple', label='Baseline Corrected', linewidth=2)
        self.line_thresh_pos, = self.ax3.plot([], [], 'r--', label=f'Hard Blink Threshold (±{HARD_BLINK_THRESH}mV)', linewidth=2)
        self.line_thresh_neg, = self.ax3.plot([], [], 'r--', linewidth=2)
        self.ax3.set_ylabel('Amplitude (mV)')
        self.ax3.set_xlabel('Time (seconds)')
        self.ax3.set_title('Hard Blink Detection - Reliable & Noise-Resistant')
        self.ax3.legend()
        self.ax3.grid(True, alpha=0.3)
        
        # Detection markers
        self.blink_markers = []
        
    def update_plot(self, frame):
        global blink_detected_flag
        
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
            
            # Check for blink detection
            if blink_detected_flag:
                self.mark_blink(HARD_BLINK_THRESH)
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
        
        # Update fixed threshold lines
        if len(times) > 0:
            thresh_pos = np.full_like(times, HARD_BLINK_THRESH)
            thresh_neg = np.full_like(times, -HARD_BLINK_THRESH)
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
            
            # Hard blink detection (every sample for responsiveness)
            blink_detected = detect_hard_blinks_only(filtered_signal, baseline, FS)
            
            # Logging (every 30th sample to reduce spam) and CSV logging
            if len(signal_buffer) % 30 == 0:
                baseline_corrected = abs(filtered_signal[-1] - baseline)
                
                # Console logging (reduced frequency)
                logging.info(
                    f"EOG: raw={eog_raw_mv:.2f}mV | filtered={filtered_signal[-1]:.2f}mV | "
                    f"baseline={baseline:.2f}mV | corrected={baseline_corrected:.2f}mV | "
                    f"thresh={HARD_BLINK_THRESH:.2f}mV"
                )
                
                # CSV logging (every sample for complete data record)
                log_to_csv(
                    current_time,
                    eog_raw_mv,
                    filtered_signal[-1],
                    baseline,
                    baseline_corrected,
                    HARD_BLINK_THRESH,
                    blink_detected=False
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
    logging.info("🚀 Starting Enhanced EOG Monitor with HARD Blink Detection")
    logging.info(f"📊 Sampling Rate: {FS} Hz")
    logging.info(f"🔧 Bandpass Filter: {EOG_HIGHPASS}-{EOG_LOWPASS} Hz")
    logging.info(f"⚡ Notch Filter: {NOTCH_F} Hz (Q={NOTCH_Q})")
    logging.info(f"🔥 Hard Blink Threshold: {HARD_BLINK_THRESH} mV")
    logging.info(f"⏰ Detection Cooldown: {DETECTION_COOLDOWN} seconds")
    logging.info(f"✅ Validation: {VALIDATION_STRICTNESS*100:.0f}% samples above {HARD_BLINK_THRESH*0.8:.1f}mV")
    logging.info(f"📈 Peak Ratio Required: {MIN_PEAK_RATIO}")
    
    # Initialize CSV logging
    init_csv_logging()
    
    try:
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
        logging.info("📊 Starting plot - Hard blink to test detection!")
        logging.info(f"📈 Data will be saved to: {csv_filename}")
        plt.show()
        
    except KeyboardInterrupt:
        logging.info("🛑 Interrupted by user")
    except Exception as e:
        logging.error(f"❌ Application error: {e}")
    finally:
        # Clean up
        global ble_connected
        ble_connected = False
        close_csv_logging()
        logging.info("✅ Application closed.")
        logging.info(f"📊 Final CSV file: {csv_filename}")

if __name__ == '__main__':
    main()