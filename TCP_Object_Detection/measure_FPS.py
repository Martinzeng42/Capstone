# COMMENT OUT THE YOLO PREDICT LINE TO SEE HOW FAST THE NETWORK PART ALONE IS.

import time
import os
import cv2
import requests
import yaml  # <-- changed here
from ultralytics import YOLO

# 1. Load Config
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(SCRIPT_DIR, "config.yaml")

if not os.path.exists(CONFIG_PATH):
    raise FileNotFoundError(f"Config file not found: {CONFIG_PATH}")

with open(CONFIG_PATH, "r") as f:
    config = yaml.safe_load(f)

# Load base IP (e.g. "http://192.168.0.153")
base_ip = config.get("ip_base_url", "http://192.168.0.153")

# Construct endpoints
ESP32_START_URL = f"{base_ip}/start_preview"
ESP32_STREAM_URL = f"{base_ip}/stream"

yolo_model_path = os.path.join(SCRIPT_DIR, config.get("yolo_model", "yolo_stuff/yolo11n.pt"))
imgsz = config.get("imgsz", 320)
classes = config.get("classes", [39, 63, 66, 67, 76])

# 2. Initialize YOLO
print(f"Loading YOLO model from: {yolo_model_path}")
model = YOLO(yolo_model_path)

# 3. Tell ESP32 to start streaming
print(f"Sending start command to ESP32: {ESP32_START_URL}")
try:
    requests.get(ESP32_START_URL, timeout=3)
    print("✅ ESP32 stream started successfully")
except Exception as e:
    print(f"⚠️ Warning: Could not send start_preview: {e}")

# 4. Connect to ESP32 MJPEG Stream
print(f"Connecting to ESP32 stream: {ESP32_STREAM_URL}")
cap = cv2.VideoCapture(ESP32_STREAM_URL)

frame_count = 0
start_time = time.time()

while True:
    ret, frame = cap.read()
    if not ret:
        print("❌ Failed to grab frame (maybe stream not active?)")
        break

    # Run YOLO inference
    #! Comment this line out to see how fast the network part alone is.
    # results = model.predict(source=frame, imgsz=imgsz, classes=classes, verbose=False)

    frame_count += 1
    elapsed = time.time() - start_time

    # Every second, print FPS and reset counters
    if elapsed >= 1.0:
        fps = frame_count / elapsed
        print(f"Actual FPS: {fps:.2f}")
        frame_count = 0
        start_time = time.time()

    # Optional: Display frame (press 'q' to quit)
    cv2.imshow("ESP32-CAM Stream", frame)
    if cv2.waitKey(1) & 0xFF == ord('q'):
        break

cap.release()
cv2.destroyAllWindows()
