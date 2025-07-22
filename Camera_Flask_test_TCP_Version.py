import socket
import struct
import cv2
import numpy as np
import threading
import time
from flask import Flask, render_template_string, jsonify, redirect, Response
import requests

app = Flask(__name__)

# ESP32-CAM endpoints
ESP32_IP = '192.168.0.164'  # Replace with your ESP32 IP
ESP32_PORT = 12345          # TCP port for ESP32 stream

latest_frame = None
running = True

# ----------------------------
# Background thread: receive TCP stream
# ----------------------------
def tcp_receiver():
    global latest_frame, running
    sock = socket.socket()
    print(f"Connecting to ESP32 TCP stream at {ESP32_IP}:{ESP32_PORT}...")
    sock.connect((ESP32_IP, ESP32_PORT))

    try:
        while running:
            # Read 4-byte frame size
            header = sock.recv(4)
            if not header:
                break
            frame_size = struct.unpack('<I', header)[0]

            # Read frame data
            frame_data = b''
            while len(frame_data) < frame_size:
                chunk = sock.recv(frame_size - len(frame_data))
                if not chunk:
                    break
                frame_data += chunk

            # Decode JPEG to NumPy image
            img = cv2.imdecode(np.frombuffer(frame_data, np.uint8), cv2.IMREAD_COLOR)
            if img is not None:
                latest_frame = img

    except Exception as e:
        print(f"TCP Receiver Error: {e}")
    finally:
        sock.close()
        print("Disconnected from ESP32")


# ----------------------------
# Flask MJPEG video generator
# ----------------------------
def mjpeg_generator():
    global latest_frame
    while True:
        if latest_frame is not None:
            # Encode as JPEG for the browser
            ret, jpeg = cv2.imencode('.jpg', latest_frame)
            if ret:
                frame_bytes = jpeg.tobytes()
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')
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
    </div>
    <div id="videoContainer" style="display:none;">
        <h3>Live Video Feed:</h3>
        <img id="videoFeed" src="/video_feed">
    </div>

    <script>
        function startStream() {
            document.getElementById('videoContainer').style.display = 'block';
            document.getElementById('videoFeed').src = "/video_feed";
        }
        function stopStream() {
            document.getElementById('videoFeed').src = "";
            document.getElementById('videoContainer').style.display = 'none';
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
    return Response(mjpeg_generator(),
                    mimetype='multipart/x-mixed-replace; boundary=frame')

# ----------------------------
# Main entry
# ----------------------------
if __name__ == '__main__':
    # Start the TCP receiver thread
    t = threading.Thread(target=tcp_receiver, daemon=True)
    t.start()

    try:
        app.run(host='0.0.0.0', port=5000, threaded=True)
    finally:
        running = False