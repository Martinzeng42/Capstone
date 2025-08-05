from pynput import keyboard, mouse
import threading
import time
import numpy as np
import matplotlib.pyplot as plt

emg_buffer = []
recording = True

# Function to simulate different EMG bursts
def simulate_emg_burst(strength=0.5, duration=20, decay_rate=10):
    return (strength * np.exp(-np.linspace(0, 1, duration) * decay_rate)).tolist()

# Map facial movement types to signal properties
movement_profiles = {
    "left_blink":   {"strength": 0.4, "duration": 15},
    "right_blink":  {"strength": 0.4, "duration": 15},
    "jaw_clench":   {"strength": 0.9, "duration": 40},
    "eyebrow":      {"strength": 0.6, "duration": 25},
    "cheek":        {"strength": 0.7, "duration": 30},
    "tongue":       {"strength": 0.8, "duration": 35},
    "eyes_closed":  {"strength": 1.0, "duration": 50},
}

# Add EMG spike based on type
def add_emg_spike(movement_type):
    profile = movement_profiles.get(movement_type, {"strength": 0.5, "duration": 20})
    print(f"⚡ {movement_type.replace('_', ' ').title()} detected")
    spike = simulate_emg_burst(strength=profile["strength"], duration=profile["duration"])
    emg_buffer.extend(spike)

# Keyboard listener with key mapping
def keyboard_listener():
    key_map = {
        '1': "left_blink",
        '2': "right_blink",
        '3': "jaw_clench",
        '4': "eyebrow",
        '5': "cheek",
        '6': "tongue",
        '7': "eyes_closed"
    }

    def on_press(key):
        try:
            char = key.char.lower()
            if char in key_map:
                add_emg_spike(key_map[char])
        except AttributeError:
            pass

    with keyboard.Listener(on_press=on_press) as listener:
        listener.join()

# Optional mouse click listener
def mouse_listener():
    def on_click(x, y, button, pressed):
        if pressed:
            add_emg_spike("jaw_clench")  # Just as example

    with mouse.Listener(on_click=on_click) as listener:
        listener.join()

# Live plotting
def emg_plot_loop():
    plt.ion()
    fig, ax = plt.subplots()
    line, = ax.plot([], [])
    ax.set_ylim(0, 1.2)
    ax.set_xlim(0, 200)

    while recording:
        if len(emg_buffer) > 200:
            emg_buffer[:] = emg_buffer[-200:]
        elif len(emg_buffer) < 200:
            emg_buffer.extend([0] * (200 - len(emg_buffer)))

        line.set_ydata(emg_buffer)
        line.set_xdata(range(len(emg_buffer)))
        fig.canvas.draw()
        fig.canvas.flush_events()
        time.sleep(0.05)

# Start threads
keyboard_thread = threading.Thread(target=keyboard_listener, daemon=True)
mouse_thread = threading.Thread(target=mouse_listener, daemon=True)
plot_thread = threading.Thread(target=emg_plot_loop, daemon=True)

keyboard_thread.start()
mouse_thread.start()
plot_thread.start()

try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    recording = False
    print("🛑 Simulation stopped.")
