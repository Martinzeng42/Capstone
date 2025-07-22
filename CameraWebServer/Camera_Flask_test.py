from flask import Flask, render_template_string, jsonify, redirect, url_for
import requests

app = Flask(__name__)

# ESP32-CAM endpoints
# ESP32_BASE = "http://192.168.0.164"  # Replace with your ESP32 IP
ESP32_BASE = "http://192.168.0.153"  # Replace with your ESP32 IP
ESP32_STREAM = f"{ESP32_BASE}/stream"
ESP32_START = f"{ESP32_BASE}/start_preview"
ESP32_STOP = f"{ESP32_BASE}/stop_preview"

HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>ESP32-CAM Control</title>
    <style>
        body { font-family: Arial, sans-serif; text-align: center; margin: 0;}
        .controls { margin-bottom: 20px; display: block; }
        button { margin: 10px; padding: 10px 20px; font-size: 16px; }
        #videoContainer { position: relative; display: none; margin: 0 auto; max-width: 640px; text-align: center; }
        img { display: block; margin: 0 auto; border: 2px solid #333; max-width: 100%; }
        canvas { position: absolute; top: 0; left: 0; pointer-events: none; }
    </style>
</head>
<body>
    <h1>ESP32-CAM Streaming</h1>
    <div class="controls">
        <form action="/start" method="get" style="display:inline-block;">
            <button type="submit">▶️ Start Preview</button>
        </form>
        <button onclick="stopPreview()">⛔ Stop Preview</button>
    </div>

    <div id="videoContainer">
        <h3>Live Video Feed:</h3>
        <!-- MJPEG stream -->
        <img id="videoFeed" src="" width="640">
        <!-- Canvas for future object detection -->
        <canvas id="overlayCanvas" width="640" height="480"></canvas>
    </div>

    <script>
        const streamUrl = "{{ esp_stream }}";

        function showStream() {
            const video = document.getElementById('videoFeed');
            video.src = streamUrl;
            document.getElementById('videoContainer').style.display = 'inline-block';
        }

        function stopPreview() {
            const video = document.getElementById('videoFeed');
            video.src = "";  // Stop fetching video
            document.getElementById('videoContainer').style.display = 'none';

            // Send async stop request to Flask (which will tell ESP32)
            fetch('/stop', { method: 'POST' })
                .then(() => console.log('Stop command sent'))
                .catch(err => console.log('Error sending stop:', err));
        }

        {% if streaming %}
        showStream();
        {% endif %}
    </script>
</body>
</html>
"""

streaming_active = False

@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE, esp_stream=ESP32_STREAM, streaming=streaming_active)

@app.route('/start')
def start_preview():
    global streaming_active
    try:
        requests.get(ESP32_START, timeout=2)  # Start ESP32 preview
        streaming_active = True
    except Exception as e:
        return f"Error starting stream: {e}<br><a href='/'>Back</a>"
    return redirect(url_for('index'))

@app.route('/stop', methods=['POST'])
def stop_preview():
    global streaming_active
    streaming_active = False
    try:
        # Tell ESP32 to stop but don’t block or crash if it times out
        requests.get(ESP32_STOP, timeout=2)
    except Exception:
        pass  # Ignore errors — just ensure Flask doesn't crash
    return jsonify({"status": "stopped"})
    
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)


# from flask import Flask, render_template_string, redirect, url_for
# import requests

# app = Flask(__name__)

# # ESP32-CAM endpoints
# ESP32_BASE = "http://192.168.0.164"  # Replace with your ESP32 IP
# ESP32_STREAM = f"{ESP32_BASE}/stream"
# ESP32_START = f"{ESP32_BASE}/start_preview"

# HTML_TEMPLATE = """
# <!DOCTYPE html>
# <html>
# <head>
#     <title>ESP32-CAM Control</title>
#     <style>
#         body { font-family: Arial, sans-serif; text-align: center; }
#         button { margin: 10px; padding: 10px 20px; font-size: 16px; }
#         #videoContainer { margin-top: 20px; position: relative; display: none; }
#         img { display: block; margin: 0 auto; border: 2px solid #333; max-width: 100%; }
#         canvas { position: absolute; top: 0; left: 0; pointer-events: none; }
#     </style>
# </head>
# <body>
#     <h1>ESP32-CAM Streaming</h1>
#     <form action="/start" method="get" style="display:inline;">
#         <button type="submit">▶️ Start Preview</button>
#     </form>
#     <button onclick="stopPreview()">⛔ Stop Preview</button>

#     <div id="videoContainer">
#         <h3>Live Video Feed:</h3>
#         <!-- MJPEG stream -->
#         <img id="videoFeed" src="" width="640">
#         <!-- Canvas for object detection overlay -->
#         <canvas id="overlayCanvas" width="640" height="480"></canvas>
#     </div>

#     <script>
#         const streamUrl = "{{ esp_stream }}";

#         function showStream() {
#             const video = document.getElementById('videoFeed');
#             video.src = streamUrl;  // Start pulling MJPEG frames
#             document.getElementById('videoContainer').style.display = 'inline-block';
#         }

#         function stopPreview() {
#             const video = document.getElementById('videoFeed');
#             video.src = "";  // Disconnect from MJPEG (pause fetching)
#             document.getElementById('videoContainer').style.display = 'none';
#         }

#         // Auto-show stream if Flask set streaming = True
#         {% if streaming %}
#         showStream();
#         {% endif %}
#     </script>
# </body>
# </html>
# """

# streaming_active = False

# @app.route('/')
# def index():
#     return render_template_string(HTML_TEMPLATE, esp_stream=ESP32_STREAM, streaming=streaming_active)

# @app.route('/start')
# def start_preview():
#     global streaming_active
#     try:
#         requests.get(ESP32_START, timeout=2)  # Start ESP32 camera
#         streaming_active = True
#     except Exception as e:
#         return f"Error starting stream: {e}<br><a href='/'>Back</a>"
#     return redirect(url_for('index'))

# # We REMOVE the /stop route completely — no call to ESP32

# if __name__ == '__main__':
#     app.run(host='0.0.0.0', port=5000)