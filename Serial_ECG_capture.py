import pandas as pd
import numpy as np
from scipy.signal import butter, filtfilt, find_peaks
import matplotlib.pyplot as plt

PATH = "Data_Log_25_07_23_00_48_16.csv"  # rename if needed

# 1) Load (auto-detect delimiter)
df = pd.read_csv(PATH, sep=None, engine="python")
df.columns = [c.strip() for c in df.columns]

t_col  = [c for c in df.columns if "time" in c.lower()][0]
ecg_col= [c for c in df.columns if any(k in c.lower() for k in ["vafe","ecg","lead"])][0]

t_us = df[t_col].to_numpy()
ecg  = df[ecg_col].astype(float).to_numpy()

fs = 1e6 / np.median(np.diff(t_us))  # Hz
print(f"Fs ≈ {fs:.1f} Hz, N={len(ecg)}")

# 2) Band-pass 0.5–40 Hz
def bandpass(x, fs, low=0.5, high=40, order=4):
    from scipy.signal import butter, filtfilt
    b,a = butter(order, [low/(fs/2), high/(fs/2)], btype="band")
    return filtfilt(b,a,x)

ecg_f = bandpass(ecg, fs)

# 3) R-peak detection
peaks, _ = find_peaks(ecg_f, distance=0.25*fs, prominence=np.std(ecg_f)*0.5)
hr_bpm = 60 * fs / np.mean(np.diff(peaks))
print(f"Estimated HR: {hr_bpm:.1f} bpm")

# 4) Plot
t_s = (t_us - t_us[0]) / 1e6
plt.plot(t_s, ecg_f)
plt.plot(t_s[peaks], ecg_f[peaks], "o")
plt.xlabel("Time (s)"); plt.ylabel("ECG (mV, filtered)")
plt.show()
