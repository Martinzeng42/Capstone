# EMG_simulator_core.py
import numpy as np
import time

# Function to simulate different EMG bursts
def simulate_emg_burst(strength=0.5, duration=20, decay_rate=10):
    return (strength * np.exp(-np.linspace(0, 1, duration) * decay_rate)).tolist()

# Stream interface to return one sample at a time
def stream_emg_data(movement_type, sampling_rate=100):
    profiles = {
        "left_blink":   {"strength": 0.4, "duration": 15},
        "right_blink":  {"strength": 0.4, "duration": 15},
        "jaw_clench":   {"strength": 0.9, "duration": 40},
        "eyebrow":      {"strength": 0.6, "duration": 25},
        "cheek":        {"strength": 0.7, "duration": 30},
        "tongue":       {"strength": 0.8, "duration": 35},
        "eyes_closed":  {"strength": 1.0, "duration": 50},
    }

    profile = profiles.get(movement_type, {"strength": 0.5, "duration": 20})
    burst = simulate_emg_burst(profile["strength"], profile["duration"])

    for sample in burst:
        yield sample
        time.sleep(1.0 / sampling_rate)
