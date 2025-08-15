# ONLY DIF is added conf score above bounding box
# Seems to be slower? tho shoudlnt be too much slower

import cv2
from ultralytics import YOLO
import asyncio
import threading
import math
import os
import requests
import yaml
import numpy as np
import time

### Load Config
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
# print("SCRIPT_DIR:", SCRIPT_DIR); assert(False)
CONFIG_PATH = os.path.join(SCRIPT_DIR, "config.yaml")
if not os.path.exists(CONFIG_PATH):
    raise FileNotFoundError(f"Config file not found: {CONFIG_PATH}")
with open(CONFIG_PATH, "r") as f:
    config = yaml.safe_load(f)

# Base IP and endpoints
base_ip = config.get("ip_base_url", "http://192.168.0.153")
ESP32_START_URL = f"{base_ip}/start_preview"
ESP32_STOP_URL = f"{base_ip}/stop_preview"
ESP32_STREAM_URL = f"{base_ip}/stream"

### Initialize YOLO model
yolo_model_path = os.path.join(SCRIPT_DIR, config.get("yolo_model", "yolo11n.pt"))
# print("yolo_model_path:", yolo_model_path); assert(False)
imgsz = config.get("imgsz", 640)
# print("imgsz:", imgsz); assert(False)
conf = config.get("conf", 0.25)
# print("conf:", conf); assert(False)
classes = config.get("classes", [39, 63, 66, 67, 76])
# print("classes:", classes); assert(False)
print(f"Loading YOLO model from: {yolo_model_path}")
model = YOLO(yolo_model_path)

# Command mappings
COMMANDS_PATH = os.path.join(SCRIPT_DIR, "commands.yaml")
if not os.path.exists(COMMANDS_PATH):
    raise FileNotFoundError(f"Commands file not found: {COMMANDS_PATH}")

with open(COMMANDS_PATH, "r") as f:
    OBJECT_COMMANDS = yaml.safe_load(f)

# Async command sender
async def send_command_async(object_name: str, command: str):
    print(f"Sending command to {object_name}: {command}")
    await asyncio.sleep(0.2)  # simulate delay
    print(f"✅ Command '{command}' sent to {object_name}")

def run_asyncio_task(coro):
    threading.Thread(target=lambda: asyncio.run(coro), daemon=True).start()

# Stream control functions
def start_stream():
    try:
        requests.get(ESP32_START_URL, timeout=3)
        print("✅ Stream started successfully")
        return True
    except Exception as e:
        print(f"⚠️ Could not start stream: {e}")
        return False

def stop_stream():
    try:
        requests.get(ESP32_STOP_URL, timeout=3)
        print("🛑 Stream stopped successfully")
        return True
    except Exception as e:
        print(f"⚠️ Could not stop stream: {e}")
        return False

# Globals for stream and detection
streaming = False
cap = None
detecting = False  # To prevent overlapping detections
highlight_box = None
highlight_label = None
highlight_conf = None  # <-- NEW: store confidence of highlighted box
highlight_duration = 0
current_commands_text = ["Press SPACE to detect"]

def run_detection_async(frame):
    """Run YOLO detection asynchronously"""
    global highlight_box, highlight_label, highlight_conf, highlight_duration, detecting, current_commands_text

    detecting = True
    results = model.predict(
        frame,
        classes=classes,    # only specific objects
        imgsz=imgsz,
        conf=conf,
        verbose=True
    )

    closest_obj = None
    closest_box = None
    closest_distance = float("inf")
    closest_conf = None
    h, w = frame.shape[:2]
    frame_center = (w // 2, h // 2)

    # Find closest detected object
    for r in results:
        # print("r.boxes:", r.boxes)    # debugging
        for box in r.boxes:
            cls = int(box.cls[0])
            # print("cls:", cls)    # debugging
            label = r.names[cls]
            # print("label:", label)    # debugging
            x1, y1, x2, y2 = map(int, box.xyxy[0])
            obj_center_x = (x1 + x2) // 2
            obj_center_y = (y1 + y2) // 2
            dist = math.dist((obj_center_x, obj_center_y), frame_center)
            if dist < closest_distance:
                closest_distance = dist
                closest_obj = label
                closest_box = (x1, y1, x2, y2)
                closest_conf = float(box.conf[0])    # conf means confidence score

    # Save the chosen detection
    highlight_box = closest_box
    # print("highlight_box:", highlight_box)   # debugging
    highlight_label = closest_obj
    highlight_conf = closest_conf
    highlight_duration = 30  # show for ~1 second

    # Prepare commands text
    if closest_obj is not None:
        cmds = OBJECT_COMMANDS.get(closest_obj, {})
        if cmds:
            lines = [f"Commands for {closest_obj}:"]
            for k, v in cmds.items():
                lines.append(f"{k}: {v}")
            current_commands_text = lines
        else:
            current_commands_text = [f"No commands for {closest_obj}"]
    else:
        current_commands_text = ["No detection"]

    detecting = False

def draw_commands_panel(frame, text_lines):
    panel_height = 150
    width = frame.shape[1]
    panel = np.zeros((panel_height, width, 3), dtype=np.uint8)
    font = cv2.FONT_HERSHEY_SIMPLEX
    font_scale = 0.6
    color = (255, 255, 255)
    line_height = 25
    y0 = 25
    for i, line in enumerate(text_lines):
        y = y0 + i * line_height
        cv2.putText(panel, line, (10, y), font, font_scale, color, 1, cv2.LINE_AA)
    combined = np.vstack((frame, panel))
    return combined

print("Controls:\n  p = toggle stream ON/OFF\n  SPACE = detect object\n  ESC = quit")
print("Note: If stream is off and you press SPACE, stream will start then detect.")

while True:
    if streaming:
        if cap is None or not cap.isOpened():
            cap = cv2.VideoCapture(ESP32_STREAM_URL)
            time.sleep(0.5)  # wait for stream to stabilize

        ret, frame = cap.read()
        if not ret:
            print("❌ Failed to grab frame, retrying...")
            if cap:
                cap.release()
                cap = None
            time.sleep(1)
            continue

        height, width = frame.shape[:2]
        frame_center = (width // 2, height // 2)
        cv2.circle(frame, frame_center, 5, (0, 0, 255), -1)

        if highlight_box and highlight_label and highlight_duration > 0:
            x1, y1, x2, y2 = highlight_box
            cv2.rectangle(frame, (x1, y1), (x2, y2), (255, 0, 0), 3)

            # Combine label + confidence (always will have a confidence if there is a highlight box)
            label_text = f"{highlight_label} {highlight_conf:.2f}"
            cv2.putText(frame, label_text, (x1, y1 - 10),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)

            highlight_duration -= 1
            if highlight_duration == 0:
                highlight_box = None
                highlight_label = None
                highlight_conf = None
        else:
            cv2.putText(frame, "Press SPACE to detect", (10, 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)

        combined_frame = draw_commands_panel(frame, current_commands_text)
        cv2.imshow("YOLO Detection", combined_frame)

    else:
        # Show paused screen
        black_frame = 255 * np.ones((480, 640, 3), dtype=np.uint8)
        cv2.putText(black_frame, "Stream Paused. Press 'p' to start.", (50, 240),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)
        cv2.imshow("YOLO Detection", black_frame)

    key = cv2.waitKey(1) & 0xFF

    if key == ord('p'):
        # Toggle stream
        if streaming:
            if cap:
                cap.release()
                cap = None
            if stop_stream():
                streaming = False
                highlight_box = None
                highlight_label = None
                highlight_conf = None
                current_commands_text = ["Press SPACE to detect"]
        else:
            if start_stream():
                streaming = True
                # Initialize capture on next loop iteration

    elif key == 27:  # ESC
        break

    elif key == 32 and not detecting:  # SPACE pressed
        if not streaming:
            # If stream is off, start it first
            if start_stream():
                streaming = True
                time.sleep(1)  # give time to start stream
                if cap:
                    cap.release()
                cap = cv2.VideoCapture(ESP32_STREAM_URL)
                time.sleep(0.5)
            else:
                print("⚠️ Could not start stream for detection")
                continue

        # Read a fresh frame for detection
        if cap and cap.isOpened():
            ret, frame_for_detection = cap.read()
            if ret:
                threading.Thread(target=run_detection_async, args=(frame_for_detection.copy(),), daemon=True).start()
            else:
                print("❌ Failed to grab frame for detection")
        else:
            print("⚠️ Stream not ready for detection")

    # Command keys (1-9) after detection
    if highlight_label and key in range(ord('1'), ord('9') + 1):
        key_str = chr(key)
        cmds_for_obj = OBJECT_COMMANDS.get(highlight_label, {})
        if key_str in cmds_for_obj:
            cmd = cmds_for_obj[key_str]
            run_asyncio_task(send_command_async(highlight_label, cmd))

# Cleanup
# print("cap:", cap)    # when the stream is paused, cap is None, and thus has no release() attribute
if cap:
    cap.release()
if streaming:
    stop_stream()
cv2.destroyAllWindows()
