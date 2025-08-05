# # ecg_simulator.py
# import numpy as np
# import time

# def generate_ecg_waveform(fs=100, duration=10):
#     """
#     Generate a simple ECG-like waveform.
#     fs: Sampling frequency (Hz)
#     duration: Duration in seconds
#     """
#     t = np.linspace(0, duration, fs * duration)
#     # Simulated ECG: periodic spikes (like R-peaks)
#     ecg = 0.6 * np.sin(2 * np.pi * 1.33 * t)       # base waveform
#     ecg += 0.2 * np.sin(2 * np.pi * 40.0 * t)      # simulate muscle/facial noise
#     ecg += 1.5 * np.exp(-100 * (t % 1.0 - 0.05)**2)  # R-peak every ~1s

#     return ecg

# def stream_simulated_ecg():
#     ecg_data = generate_ecg_waveform()
#     for value in ecg_data:
#         yield int(value * 1000)  # Convert to millivolts (for realism)

# if __name__ == "__main__":
#     for value in stream_simulated_ecg():
#         print(f"ECG: {value} mV")
#         time.sleep(0.01)  # 100 Hz sampling rate


import neurokit2 as nk
import matplotlib.pyplot as plt

# Simulate ECG
simulated_ecg = nk.ecg_simulate(duration=8, sampling_rate=200, heart_rate=30)

# Plot the ECG
nk.signal_plot(simulated_ecg, sampling_rate=200)
plt.show()  # <- This is essential to display the plot outside notebooks
