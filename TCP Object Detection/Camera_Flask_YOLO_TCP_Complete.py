#######------------------------With FPS count--------------------------#######
# import socket
# import struct
# import cv2
# import numpy as np
# import threading
# import time
# from flask import Flask, render_template_string, jsonify, Response

# app = Flask(__name__)

# ESP32_IP = '192.168.0.164'
# ESP32_PORT = 12345

# latest_frame = None
# running = True
# current_fps = 0.0  # Shared variable to store latest FPS

# # ----------------------------
# # Background thread: receive TCP stream
# # ----------------------------
# def tcp_receiver():
#     global latest_frame, running
#     sock = socket.socket()
#     print(f"Connecting to ESP32 TCP stream at {ESP32_IP}:{ESP32_PORT}...")
#     sock.connect((ESP32_IP, ESP32_PORT))

#     try:
#         while running:
#             header = sock.recv(4)
#             if not header:
#                 break
#             frame_size = struct.unpack('<I', header)[0]

#             frame_data = b''
#             while len(frame_data) < frame_size:
#                 chunk = sock.recv(frame_size - len(frame_data))
#                 if not chunk:
#                     break
#                 frame_data += chunk

#             img = cv2.imdecode(np.frombuffer(frame_data, np.uint8), cv2.IMREAD_COLOR)
#             if img is not None:
#                 latest_frame = img

#     except Exception as e:
#         print(f"TCP Receiver Error: {e}")
#     finally:
#         sock.close()
#         print("Disconnected from ESP32")

# # ----------------------------
# # Flask MJPEG video generator with FPS counter
# # ----------------------------
# def mjpeg_generator():
#     global latest_frame, current_fps
#     frame_count = 0
#     start_time = time.time()

#     while True:
#         if latest_frame is not None:
#             ret, jpeg = cv2.imencode('.jpg', latest_frame)
#             if ret:
#                 frame_bytes = jpeg.tobytes()
#                 yield (b'--frame\r\n'
#                        b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')
#                 frame_count += 1

#                 elapsed = time.time() - start_time
#                 if elapsed >= 1.0:
#                     current_fps = frame_count / elapsed
#                     frame_count = 0
#                     start_time = time.time()
#         else:
#             time.sleep(0.01)

# # ----------------------------
# # Flask routes
# # ----------------------------
# HTML_TEMPLATE = """
# <!DOCTYPE html>
# <html>
# <head>
#     <title>ESP32-CAM TCP Streaming</title>
#     <style>
#         body { font-family: Arial, sans-serif; text-align: center; }
#         .controls { margin-bottom: 20px; display: block; }
#         button { margin: 10px; padding: 10px 20px; font-size: 16px; }
#         #videoContainer { margin: 0 auto; max-width: 640px; text-align: center; }
#         img { width: 100%; border: 2px solid #333; }
#     </style>
# </head>
# <body>
#     <h1>ESP32-CAM (TCP) Streaming</h1>
#     <div class="controls">
#         <button onclick="startStream()">Start Preview</button>
#         <button onclick="stopStream()">Stop Preview</button>
#     </div>
#     <div id="videoContainer" style="display:none;">
#         <h3>Live Video Feed:</h3>
#         <img id="videoFeed" src="/video_feed">
#         <p><strong>FPS:</strong> <span id="fps">0.00</span></p>
#     </div>

#     <script>
#         function startStream() {
#             document.getElementById('videoContainer').style.display = 'block';
#             document.getElementById('videoFeed').src = "/video_feed";
#             startFPSPolling();
#         }
#         function stopStream() {
#             document.getElementById('videoFeed').src = "";
#             document.getElementById('videoContainer').style.display = 'none';
#             stopFPSPolling();
#         }

#         let fpsInterval;

#         function startFPSPolling() {
#             fpsInterval = setInterval(() => {
#                 fetch('/fps')
#                     .then(response => response.json())
#                     .then(data => {
#                         document.getElementById('fps').textContent = data.fps.toFixed(2);
#                     });
#             }, 1000);
#         }

#         function stopFPSPolling() {
#             clearInterval(fpsInterval);
#         }
#     </script>
# </body>
# </html>
# """

# @app.route('/')
# def index():
#     return render_template_string(HTML_TEMPLATE)

# @app.route('/video_feed')
# def video_feed():
#     return Response(mjpeg_generator(),
#                     mimetype='multipart/x-mixed-replace; boundary=frame')

# @app.route('/fps')
# def get_fps():
#     return jsonify(fps=current_fps)

# # ----------------------------
# # Main entry
# # ----------------------------
# if __name__ == '__main__':
#     t = threading.Thread(target=tcp_receiver, daemon=True)
#     t.start()

#     try:
#         app.run(host='0.0.0.0', port=5000, threaded=True)
#     finally:
#         running = False


# import socket
# import struct
# import cv2
# import numpy as np
# import threading
# import time
# import requests
# import os
# import yaml
# import asyncio
# import threading
# import math
# from ultralytics import YOLO
# from flask import Flask, render_template_string, jsonify, Response

# app = Flask(__name__)

# # ================== YOLO + COMMAND UTILITIES ==================

# # ----------------------------
# # CONFIG LOADING
# # ----------------------------
# SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
# CONFIG_PATH = os.path.join(SCRIPT_DIR, "config.yaml")
# if not os.path.exists(CONFIG_PATH):
#     raise FileNotFoundError(f"Config file not found: {CONFIG_PATH}")
# with open(CONFIG_PATH, "r") as f:
#     config = yaml.safe_load(f)


# # ----------------------------
# # YOLO MODEL SETUP
# # ----------------------------
# yolo_model_path = os.path.join(SCRIPT_DIR, config.get("yolo_model", "yolo11n.pt"))
# imgsz = config.get("imgsz", 640)
# conf = config.get("conf", 0.25)
# classes = config.get("classes", [39, 63, 66, 67, 76])
# model = YOLO(yolo_model_path)
# # Globals for stream and detection
# highlight_box = None
# highlight_label = None
# highlight_conf = None  # <-- NEW: store confidence of highlighted box
# highlight_duration = 0
# current_commands_text = ["Press SPACE to detect"]

# # ----------------------------
# # LOAD COMMAND MAPPINGS
# # ----------------------------
# COMMANDS_PATH = os.path.join(SCRIPT_DIR, "commands.yaml")
# if not os.path.exists(COMMANDS_PATH):
#     raise FileNotFoundError(f"Commands file not found: {COMMANDS_PATH}")
# with open(COMMANDS_PATH, "r") as f:
#     OBJECT_COMMANDS = yaml.safe_load(f)


# # ----------------------------
# # ASYNC COMMAND SENDER
# # ----------------------------
# async def send_command_async(object_name: str, command: str):
#     print(f"Sending command to {object_name}: {command}")
#     await asyncio.sleep(0.2)
#     print(f"Command '{command}' sent to {object_name}'")

# def run_asyncio_task(coro):
#     threading.Thread(target=lambda: asyncio.run(coro), daemon=True).start()


# # ----------------------------
# # YOLO DETECTION FUNCTION
# # ----------------------------
# def detect_and_highlight(frame):
#     global highlight_box, highlight_label, highlight_conf, highlight_duration, current_commands_text, latest_frame

#     results = model.predict(frame, classes=classes, imgsz=imgsz, conf=conf, verbose=False)
#     h, w = frame.shape[:2]
#     frame_center = (w // 2, h // 2)

#     closest_box = None
#     closest_label = None
#     closest_conf = None
#     closest_distance = float("inf")

#     for r in results:
#         for box in r.boxes:
#             cls = int(box.cls[0])
#             label = r.names[cls]
#             x1, y1, x2, y2 = map(int, box.xyxy[0])
#             obj_center = ((x1 + x2) // 2, (y1 + y2) // 2)
#             dist = math.dist(obj_center, frame_center)
#             if dist < closest_distance:
#                 closest_box = (x1, y1, x2, y2)
#                 closest_label = label
#                 closest_conf = float(box.conf[0])
#                 closest_distance = dist

#     if closest_box:
#         x1, y1, x2, y2 = closest_box
#         cv2.rectangle(frame, (x1, y1), (x2, y2), (255, 0, 0), 3)
#         label_text = f"{closest_label} {closest_conf:.2f}"
#         cv2.putText(frame, label_text, (x1, y1 - 10),
#                     cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)

#         highlight_box = closest_box
#         highlight_label = closest_label
#         highlight_conf = closest_conf
#         highlight_duration = 30

#         cmds = OBJECT_COMMANDS.get(closest_label, {})
#         if cmds:
#             lines = [f"Commands for {closest_label}:"]
#             for k, v in cmds.items():
#                 lines.append(f"{k}: {v}")
#             current_commands_text = lines
#         else:
#             current_commands_text = [f"No commands for {closest_label}"]
#     else:
#         current_commands_text = ["No detection"]

#     return latest_frame


# # ----------------------------
# # COMMAND PANEL OVERLAY
# # ----------------------------
# def draw_commands_panel(frame, text_lines):
#     panel_height = 150
#     width = frame.shape[1]
#     panel = np.zeros((panel_height, width, 3), dtype=np.uint8)
#     font = cv2.FONT_HERSHEY_SIMPLEX
#     font_scale = 0.6
#     color = (255, 255, 255)
#     line_height = 25
#     y0 = 25
#     for i, line in enumerate(text_lines):
#         y = y0 + i * line_height
#         cv2.putText(panel, line, (10, y), font, font_scale, color, 1, cv2.LINE_AA)
#     combined = np.vstack((frame, panel))
#     return combined


# # =============================================================

# ESP32_IP = '192.168.0.164'
# ESP32_PORT = 12345

# latest_frame = None
# streaming_active = False
# tcp_thread = None
# yolo_thread = None
# sock = None
# current_fps = 0.0

# # ----------------------------
# # TCP receiver thread
# # ----------------------------
# def tcp_receiver():
#     global latest_frame, streaming_active, sock, current_commands_text 
#     global highlight_box, highlight_label, highlight_conf, highlight_duration
#     global yolo_thread
#     try:
#         sock = socket.socket()
#         print(f"Connecting to ESP32 TCP stream at {ESP32_IP}:{ESP32_PORT}...")
#         sock.connect((ESP32_IP, ESP32_PORT))

#         while streaming_active:
#             header = sock.recv(4)
#             if not header:
#                 break
#             frame_size = struct.unpack('<I', header)[0]

#             frame_data = b''
#             while len(frame_data) < frame_size:
#                 chunk = sock.recv(frame_size - len(frame_data))
#                 if not chunk:
#                     break
#                 frame_data += chunk

#             img = cv2.imdecode(np.frombuffer(frame_data, np.uint8), cv2.IMREAD_COLOR)
#             height, width = img.shape[:2]
#             frame_center = (width // 2, height // 2)
#             cv2.circle(img, frame_center, 5, (0, 0, 255), -1)

#             if highlight_box and highlight_label and highlight_duration > 0:
#                 x1, y1, x2, y2 = highlight_box
#                 cv2.rectangle(img, (x1, y1), (x2, y2), (255, 0, 0), 3)

#                 # Combine label + confidence (always will have a confidence if there is a highlight box)
#                 label_text = f"{highlight_label} {highlight_conf:.2f}"
#                 cv2.putText(img, label_text, (x1, y1 - 10),
#                             cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)

#                 highlight_duration -= 1
#                 if highlight_duration == 0:
#                     highlight_box = None
#                     highlight_label = None
#                     highlight_conf = None
#             else:
#                 cv2.putText(img, "Press SPACE to detect", (10, 30),
#                             cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)
#             key = cv2.waitKey(1) & 0xFF
#             if img is not None:
#                 temp_img = img.copy()
#                 if key == 32:
#                     print("🧠 Triggering manual detection...")
#                     yolo_thread = threading.Thread(target=run_detection_async, args=(temp_img,), daemon=True).start()
#                 # temp_img = detect_and_highlight(latest_frame)  
#                 combined_frame = draw_commands_panel(temp_img, current_commands_text)
#                 latest_frame = combined_frame
#                 # latest_frame = img

#     except Exception as e:
#         print(f"TCP Receiver Error: {e}")
#     finally:
#         if sock:
#             try:
#                 sock.shutdown(socket.SHUT_RDWR)
#             except:
#                 pass
#             sock.close()
#             sock = None
#             print("Disconnected from ESP32")

# # ----------------------------
# # MJPEG generator with FPS
# # ----------------------------
# def mjpeg_generator():
#     global latest_frame, current_fps
#     frame_count = 0
#     start_time = time.time()

#     while True:
#         if latest_frame is not None:
#             ret, jpeg = cv2.imencode('.jpg', latest_frame)
#             if ret:
#                 frame_bytes = jpeg.tobytes()
#                 yield (b'--frame\r\n'
#                        b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')
#                 frame_count += 1

#                 elapsed = time.time() - start_time
#                 if elapsed >= 1.0:
#                     current_fps = frame_count / elapsed
#                     frame_count = 0
#                     start_time = time.time()
#         else:
#             time.sleep(0.01)

# # ----------------------------
# # Flask routes
# # ----------------------------
# HTML_TEMPLATE = """
# <!DOCTYPE html>
# <html>
# <head>
#     <title>ESP32-CAM TCP Streaming</title>
#     <style>
#         body { font-family: Arial, sans-serif; text-align: center; }
#         .controls { margin-bottom: 20px; display: block; }
#         button { margin: 10px; padding: 10px 20px; font-size: 16px; }
#         #videoContainer { margin: 0 auto; max-width: 640px; text-align: center; }
#         img { width: 100%; border: 2px solid #333; }
#     </style>
# </head>
# <body>
#     <h1>ESP32-CAM (TCP) Streaming</h1>
#     <div class="controls">
#         <button onclick="startStream()">Start Preview</button>
#         <button onclick="stopStream()">Stop Preview</button>
#     </div>
#     <div id="videoContainer" style="display:none;">
#         <h3>Live Video Feed:</h3>
#         <img id="videoFeed" src="/video_feed">
#         <p><strong>FPS:</strong> <span id="fps">0.00</span></p>
#     </div>

#     <script>
#         function triggerDetection() {
#             fetch('/trigger_detection').then(() => {
#             console.log("Detection triggered");
#             });
#         }

#         function startStream() {
#             fetch('/start_stream').then(() => {
#                 document.getElementById('videoContainer').style.display = 'block';
#                 document.getElementById('videoFeed').src = "/video_feed";
#                 startFPSPolling();
#             });
#         }

#         function stopStream() {
#             fetch('/stop_stream').then(() => {
#                 document.getElementById('videoFeed').src = "";
#                 document.getElementById('videoContainer').style.display = 'none';
#                 stopFPSPolling();
#             });
#         }

#         let fpsInterval;
#         function startFPSPolling() {
#             fpsInterval = setInterval(() => {
#                 fetch('/fps')
#                     .then(res => res.json())
#                     .then(data => {
#                         document.getElementById('fps').textContent = data.fps.toFixed(2);
#                     });
#             }, 1000);
#         }

#         function stopFPSPolling() {
#             clearInterval(fpsInterval);
#         }
#     </script>
# </body>
# </html>
# """

# @app.route('/')
# def index():
#     return render_template_string(HTML_TEMPLATE)

# @app.route('/video_feed')
# def video_feed():
#     return Response(mjpeg_generator(),
#                     mimetype='multipart/x-mixed-replace; boundary=frame')

# @app.route('/fps')
# def get_fps():
#     return jsonify(fps=current_fps)

# @app.route('/start_stream')
# def start_stream():
#     global streaming_active, tcp_thread
#     if not streaming_active:
#         streaming_active = True
#         tcp_thread = threading.Thread(target=tcp_receiver, daemon=True)
#         tcp_thread.start()
#         print("Stream started.")
#     return jsonify(status='started')

# @app.route('/stop_stream')
# def stop_stream():
#     global streaming_active, sock, tcp_thread
#     if (tcp_thread and tcp_thread.is_alive()) or (yolo_thread and yolo_thread.is_alive()) :
#         print("[INFO] Waiting for previous stream thread to end...")
#         streaming_active = False
#         tcp_thread.join()  # wait for the thread to finish
#         yolo_thread.join()
#         if sock:
#             try:
#                 sock.shutdown(socket.SHUT_RDWR)
#                 sock.close()
#             except:
#                 pass
#     sock = None
#     print("Stream stopped.")
#     return jsonify(status='stopped')

# # ----------------------------
# # Run app
# # ----------------------------
# if __name__ == '__main__':
#     app.run(host='0.0.0.0', port=5000, threaded=True)


import socket
import struct
import cv2
import numpy as np
import threading
import time
import requests
import os
import yaml
import asyncio
import math
from ultralytics import YOLO
from flask import Flask, render_template_string, jsonify, Response

app = Flask(__name__)

# ================== YOLO + COMMAND UTILITIES ==================

# ----------------------------
# CONFIG LOADING
# ----------------------------
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(SCRIPT_DIR, "config.yaml")
if not os.path.exists(CONFIG_PATH):
    raise FileNotFoundError(f"Config file not found: {CONFIG_PATH}")
with open(CONFIG_PATH, "r") as f:
    config = yaml.safe_load(f)

# ----------------------------
# YOLO MODEL SETUP
# ----------------------------
yolo_model_path = os.path.join(SCRIPT_DIR, config.get("yolo_model", "yolo11n.pt"))
imgsz = config.get("imgsz", 640)
conf = config.get("conf", 0.25)
classes = config.get("classes", [39, 63, 66, 67, 76])
model = YOLO(yolo_model_path)

highlight_box = None
highlight_label = None
highlight_conf = None
highlight_duration = 0
current_commands_text = ["Press SPACE to detect"]

# ----------------------------
# LOAD COMMAND MAPPINGS
# ----------------------------
COMMANDS_PATH = os.path.join(SCRIPT_DIR, "commands.yaml")
if not os.path.exists(COMMANDS_PATH):
    raise FileNotFoundError(f"Commands file not found: {COMMANDS_PATH}")
with open(COMMANDS_PATH, "r") as f:
    OBJECT_COMMANDS = yaml.safe_load(f)

# ----------------------------
# ASYNC COMMAND SENDER
# ----------------------------
async def send_command_async(object_name: str, command: str):
    print(f"Sending command to {object_name}: {command}")
    await asyncio.sleep(0.2)
    print(f"Command '{command}' sent to {object_name}'")

def run_asyncio_task(coro):
    threading.Thread(target=lambda: asyncio.run(coro), daemon=True).start()

# ----------------------------
# YOLO DETECTION FUNCTION
# ----------------------------
def detect_and_highlight(frame):
    global highlight_box, highlight_label, highlight_conf, highlight_duration, current_commands_text, latest_frame

    results = model.predict(frame, classes=classes, imgsz=imgsz, conf=conf, verbose=False)
    h, w = frame.shape[:2]
    frame_center = (w // 2, h // 2)

    closest_box = None
    closest_label = None
    closest_conf = None
    closest_distance = float("inf")

    for r in results:
        for box in r.boxes:
            cls = int(box.cls[0])
            label = r.names[cls]
            x1, y1, x2, y2 = map(int, box.xyxy[0])
            obj_center = ((x1 + x2) // 2, (y1 + y2) // 2)
            dist = math.dist(obj_center, frame_center)
            if dist < closest_distance:
                closest_box = (x1, y1, x2, y2)
                closest_label = label
                closest_conf = float(box.conf[0])
                closest_distance = dist

    if closest_box:
        x1, y1, x2, y2 = closest_box
        cv2.rectangle(frame, (x1, y1), (x2, y2), (255, 0, 0), 3)
        label_text = f"{closest_label} {closest_conf:.2f}"
        cv2.putText(frame, label_text, (x1, y1 - 10),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)

        highlight_box = closest_box
        highlight_label = closest_label
        highlight_conf = closest_conf
        highlight_duration = 30

        cmds = OBJECT_COMMANDS.get(closest_label, {})
        if cmds:
            lines = [f"Commands for {closest_label}:"]
            for k, v in cmds.items():
                lines.append(f"{k}: {v}")
            current_commands_text = lines
        else:
            current_commands_text = [f"No commands for {closest_label}"]
    else:
        current_commands_text = ["No detection"]

    return frame

# ----------------------------
# DETECTION WRAPPER
# ----------------------------
def run_detection_async(frame):
    global latest_frame
    result_frame = detect_and_highlight(frame)
    latest_frame = result_frame

# ----------------------------
# COMMAND PANEL OVERLAY
# ----------------------------
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

# =============================================================

ESP32_IP = '192.168.0.164'
ESP32_PORT = 12345

latest_frame = None
streaming_active = False
tcp_thread = None
yolo_thread = None
sock = None
current_fps = 0.0

# ----------------------------
# TCP receiver thread
# ----------------------------
def tcp_receiver():
    global latest_frame, streaming_active, sock, current_commands_text 
    global highlight_box, highlight_label, highlight_conf, highlight_duration
    global yolo_thread
    try:
        sock = socket.socket()
        print(f"Connecting to ESP32 TCP stream at {ESP32_IP}:{ESP32_PORT}...")
        sock.connect((ESP32_IP, ESP32_PORT))

        while streaming_active:
            header = sock.recv(4)
            if not header:
                break
            frame_size = struct.unpack('<I', header)[0]

            frame_data = b''
            while len(frame_data) < frame_size:
                chunk = sock.recv(frame_size - len(frame_data))
                if not chunk:
                    break
                frame_data += chunk

            img = cv2.imdecode(np.frombuffer(frame_data, np.uint8), cv2.IMREAD_COLOR)
            height, width = img.shape[:2]
            frame_center = (width // 2, height // 2)
            cv2.circle(img, frame_center, 5, (0, 0, 255), -1)

            if highlight_box and highlight_label and highlight_duration > 0:
                x1, y1, x2, y2 = highlight_box
                cv2.rectangle(img, (x1, y1), (x2, y2), (255, 0, 0), 3)
                label_text = f"{highlight_label} {highlight_conf:.2f}"
                cv2.putText(img, label_text, (x1, y1 - 10),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
                highlight_duration -= 1
                if highlight_duration == 0:
                    highlight_box = None
                    highlight_label = None
                    highlight_conf = None
            else:
                cv2.putText(img, "Press SPACE to detect", (10, 30),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)

            if img is not None:
                temp_img = img.copy()
                combined_frame = draw_commands_panel(temp_img, current_commands_text)
                latest_frame = combined_frame

    except Exception as e:
        print(f"TCP Receiver Error: {e}")
    finally:
        if sock:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except:
                pass
            sock.close()
            sock = None
            print("Disconnected from ESP32")

# ----------------------------
# MJPEG generator with FPS
# ----------------------------
def mjpeg_generator():
    global latest_frame, current_fps
    frame_count = 0
    start_time = time.time()

    while True:
        if latest_frame is not None:
            ret, jpeg = cv2.imencode('.jpg', latest_frame)
            if ret:
                frame_bytes = jpeg.tobytes()
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')
                frame_count += 1

                elapsed = time.time() - start_time
                if elapsed >= 1.0:
                    current_fps = frame_count / elapsed
                    frame_count = 0
                    start_time = time.time()
        else:
            time.sleep(0.01)

# ----------------------------
# Flask routes
# ----------------------------
HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>ESP32-CAM TCP Streaming</title>
    <style>
        body { font-family: Arial, sans-serif; text-align: center; }
        .controls { margin-bottom: 20px; display: block; }
        button { margin: 10px; padding: 10px 20px; font-size: 16px; }
        #videoContainer { margin: 0 auto; max-width: 640px; text-align: center; }
        img { width: 100%; border: 2px solid #333; }
    </style>
</head>
<body>
    <h1>ESP32-CAM (TCP) Streaming</h1>
    <div class="controls">
        <button onclick="startStream()">Start Preview</button>
        <button onclick="stopStream()">Stop Preview</button>
        <button onclick="triggerDetection()">Trigger Detection</button>
    </div>
    <div id="videoContainer" style="display:none;">
        <h3>Live Video Feed:</h3>
        <img id="videoFeed" src="/video_feed">
        <p><strong>FPS:</strong> <span id="fps">0.00</span></p>
    </div>
    <script>
        function triggerDetection() {
            fetch('/trigger_detection').then(() => {
                console.log("Detection triggered");
            });
        }
        function startStream() {
            fetch('/start_stream').then(() => {
                document.getElementById('videoContainer').style.display = 'block';
                document.getElementById('videoFeed').src = "/video_feed";
                startFPSPolling();
            });
        }
        function stopStream() {
            fetch('/stop_stream').then(() => {
                document.getElementById('videoFeed').src = "";
                document.getElementById('videoContainer').style.display = 'none';
                stopFPSPolling();
            });
        }
        let fpsInterval;
        function startFPSPolling() {
            fpsInterval = setInterval(() => {
                fetch('/fps')
                    .then(res => res.json())
                    .then(data => {
                        document.getElementById('fps').textContent = data.fps.toFixed(2);
                    });
            }, 1000);
        }
        function stopFPSPolling() {
            clearInterval(fpsInterval);
        }
    </script>
</body>
</html>
"""

@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)

@app.route('/video_feed')
def video_feed():
    return Response(mjpeg_generator(), mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/fps')
def get_fps():
    return jsonify(fps=current_fps)

@app.route('/start_stream')
def start_stream():
    global streaming_active, tcp_thread
    if not streaming_active:
        streaming_active = True
        tcp_thread = threading.Thread(target=tcp_receiver, daemon=True)
        tcp_thread.start()
        print("Stream started.")
    return jsonify(status='started')

@app.route('/stop_stream')
def stop_stream():
    global streaming_active, sock, tcp_thread
    if (tcp_thread and tcp_thread.is_alive()) or (yolo_thread and yolo_thread.is_alive()):
        print("[INFO] Waiting for previous stream thread to end...")
        streaming_active = False
        tcp_thread.join()
        yolo_thread.join()
        if sock:
            try:
                sock.shutdown(socket.SHUT_RDWR)
                sock.close()
            except:
                pass
    sock = None
    print("Stream stopped.")
    return jsonify(status='stopped')

@app.route('/trigger_detection')
def trigger_detection():
    global latest_frame, yolo_thread
    if latest_frame is not None:
        print("Triggered detection from webpage")
        yolo_thread = threading.Thread(target=run_detection_async, args=(latest_frame.copy(),), daemon=True)
        yolo_thread.start()
        return jsonify(status='detection_triggered')
    else:
        print("⚠️ No frame available to run detection.")
        return jsonify(status='no_frame_available')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, threaded=True)