import paho.mqtt.client as mqtt

# MQTT setup
MQTT_BROKER = "broker.hivemq.com"  # Public test broker
MQTT_PORT = 1883
MQTT_TOPIC = "emg/control"
mqtt_client = mqtt.Client()
mqtt_client.connect(MQTT_BROKER, MQTT_PORT, 60)

# Extended movement profile with action
movement_profiles = {
    "left_blink":   {"strength": 0.4, "duration": 15, "action": "light_on"},
    "right_blink":  {"strength": 0.4, "duration": 15, "action": "light_off"},
    "jaw_clench":   {"strength": 0.9, "duration": 40, "action": "fan_toggle"},
    "eyebrow":      {"strength": 0.6, "duration": 25, "action": "buzzer_toggle"},
    "cheek":        {"strength": 0.7, "duration": 30, "action": "spray"},
    "tongue":       {"strength": 0.8, "duration": 35, "action": "sound_alarm"},
    "eyes_closed":  {"strength": 1.0, "duration": 50, "action": "shutdown"},
}

def add_emg_spike(movement_type):
    profile = movement_profiles.get(movement_type, {"strength": 0.5, "duration": 20, "action": "noop"})
    print(f"⚡ {movement_type.replace('_', ' ').title()} detected → Action: {profile['action']}")
    spike = simulate_emg_burst(profile["strength"], profile["duration"])
    emg_buffer.extend(spike)
    mqtt_client.publish(MQTT_TOPIC, profile["action"])
