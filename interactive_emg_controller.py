# interactive_emg_controller.py

from pynput import keyboard, mouse
import threading
import numpy as np
import time
import paho.mqtt.client as mqtt
import matplotlib.pyplot as plt

# ========== CONFIGURATION ==========
MQTT_BROKER = "broker.hivemq.com"
MQTT_PORT = 1883
MQTT_TOPIC = "emg/control"
THRESHOLD = 0.75
COOLDOWN = 1.0  # seconds

# ========== MQTT SETUP ==========
mqtt_client = mqtt.Client()
mqtt_client.connect(MQTT_BROKER, MQTT_PORT, 60)

# ========== EMG SIMULATION ==========
emg_buffer = []
simulated_queue = []
recording = True
last_trigger = 0

movement_profiles = {
    '1': ("left_blink", 0.4, 15, "light_on"),
    '2': ("right_blink", 0.4, 15, "light_off"),
    '3': ("jaw_clench", 0.9, 40, "fan_toggle"),
    '4': ("eyebrow", 0.6, 25, "buzzer_toggle"),
    '5': ("cheek", 0.7, 30, "spray"),
    '6': ("tongue", 0.8, 35, "sound_alarm"),
    '7': ("eyes_closed", 1.0, 50, "shutdown"),
    '8': ("smile", 0.5, 20, "volume_up"),
    '9': ("frown", 0.5, 20, "volume_down"),
    'mouse': ("jaw_clench", 0.9, 40, "fan_toggle")
}

def simulate_emg_burst(strength=0.5, duration=20, decay=10):
    return (strength * np.exp(-np.linspace(0, 1, duration) * decay)).tolist()

def trigger_action(action):
    global last_trigger
    now = time.time()
    if now - last_trigger > COOLDOWN:
        print(f"Triggered: {action}")
        mqtt_client.publish(MQTT_TOPIC, action)
        last_trigger = now

def keyboard_listener():
    def on_press(key):
        try:
            char = key.char
            if char in movement_profiles:
                movement, strength, duration, action = movement_profiles[char]
                print(f"Simulated {movement} → {action}")
                simulated_queue.extend(simulate_emg_burst(strength, duration))
                trigger_action(action)
        except AttributeError:
            pass
    with keyboard.Listener(on_press=on_press) as listener:
        listener.join()

def mouse_listener():
    def on_click(x, y, button, pressed):
        if pressed:
            movement, strength, duration, action = movement_profiles["mouse"]
            print(f"Mouse click → {action}")
            simulated_queue.extend(simulate_emg_burst(strength, duration))
            trigger_action(action)
    with mouse.Listener(on_click=on_click) as listener:
        listener.join()

# ========== EMG STREAM PROCESSING ==========
def emg_processing_loop():
    global recording
    plt.ion()
    fig, ax = plt.subplots()
    line, = ax.plot([], [])
    ax.set_ylim(0, 1.2)
    ax.set_xlim(0, 200)

    while recording:
        if simulated_queue:
            sample = simulated_queue.pop(0)
        else:
            sample = 0.0

        emg_buffer.append(sample)
        if len(emg_buffer) > 200:
            emg_buffer[:] = emg_buffer[-200:]

        # Plot update
        line.set_ydata(emg_buffer)
        line.set_xdata(range(len(emg_buffer)))
        fig.canvas.draw()
        fig.canvas.flush_events()

        # Real-time threshold detection
        if sample > THRESHOLD:
            trigger_action("spike_detected")

        time.sleep(0.01)

# ========== START THREADS ==========
keyboard_thread = threading.Thread(target=keyboard_listener, daemon=True)
mouse_thread = threading.Thread(target=mouse_listener, daemon=True)
processing_thread = threading.Thread(target=emg_processing_loop, daemon=True)

keyboard_thread.start()
mouse_thread.start()
processing_thread.start()

try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    recording = False
    print("Simulation stopped.")
