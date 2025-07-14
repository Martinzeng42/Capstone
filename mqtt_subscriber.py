# mqtt_subscriber.py
import paho.mqtt.client as mqtt

def on_message(client, userdata, msg):
    print(f"Received on {msg.topic}: {msg.payload.decode()}")

client = mqtt.Client()
client.on_message = on_message
client.connect("broker.hivemq.com", 1883, 60)
client.subscribe("emg/control")

print("Listening for EMG control messages...")
client.loop_forever()
