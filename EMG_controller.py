# EMG_controller.py
import paho.mqtt.client as mqtt
from EMG_simulator_core import stream_emg_data

# MQTT Setup
MQTT_BROKER = "broker.hivemq.com"
MQTT_PORT = 1883
MQTT_TOPIC = "emg/control"

mqtt_client = mqtt.Client()
mqtt_client.connect(MQTT_BROKER, MQTT_PORT, 60)

# Control thresholds
THRESHOLD = 0.75  # When EMG signal crosses this, trigger
COOLDOWN = 1.0    # Seconds between triggers

import time
last_trigger = 0

def trigger_action(action):
    global last_trigger
    now = time.time()
    if now - last_trigger < COOLDOWN:
        return  # Prevent spam
    print(f"⚡ Triggered: {action}")
    mqtt_client.publish(MQTT_TOPIC, action)
    last_trigger = now

def run_emg_control(movement="jaw_clench"):
    print(f"📡 Listening to simulated EMG: {movement}")
    for value in stream_emg_data(movement_type=movement):
        print(f"EMG: {value:.3f}")
        if value > THRESHOLD:
            trigger_action("toggle_light")

if __name__ == "__main__":
    run_emg_control("jaw_clench")  # Change movement here (e.g., "left_blink")
