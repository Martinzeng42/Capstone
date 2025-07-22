# "tutorial" link: https://docs.ultralytics.com/usage/python/

import cv2
from PIL import Image

from ultralytics import YOLO

# Models: all versions have n, s, m, l, x sizes. On CPU, only n is fast enough; other sizes are too slow (need a GPU).
# Changing the model is super easy, just change the model name below. It AUTO DOWNLOADS the model if it's missing.
# List of models: https://docs.ultralytics.com/models/
model = YOLO("Object_Detection/yolo11n.pt")

# =============================
# New addition from modified CameraWebServer code
import requests
try:
    requests.get("http://192.168.0.153/start_preview", timeout=3)
    print("✅ ESP32 stream started successfully")
except Exception as e:
    print(f"⚠️ Warning: Could not send start_preview: {e}")
# New code END
# =============================

ip_stream_url = "http://192.168.0.153/stream"     # check Arduino IDE Serial Monitor for your url
# accepts all formats - image/dir/Path/URL/video/PIL/ndarray. 0 for webcam
# results = model.predict(source="0", show=True)
results = model.predict(source=ip_stream_url, show=True)
