from flask import Flask, render_template_string, redirect, url_for
import threading
import socket

app = Flask(__name__)
device_state = {"on": False}
client_info = {"ip": None, "port": None}  # to store sender info


HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Device Status</title>
    <meta http-equiv="refresh" content="1">
    <style>
        body {
            background-color: {{ 'blue' if state else 'red' }};
            color: white;
            text-align: center;
            font-family: Arial, sans-serif;
            padding-top: 100px;
            font-size: 36px;
        }
    </style>
</head>
<body>
    <h1>{{ 'Turn ON' if state else 'Turn OFF' }}</h1>
</body>
</html>
"""


@app.route("/")
def home():
    return render_template_string(HTML_TEMPLATE, state=device_state["on"])

@app.route("/ready")
def ready():
    return "Device is ready"

@app.route("/turnon")
def turn_on():
    set_device_state(True)
    return redirect(url_for('home'))

@app.route("/turnoff")
def turn_off():
    set_device_state(False)
    return redirect(url_for('home'))

# 🔧 Modular function to set device state
def set_device_state(state: bool):
    device_state["on"] = state
    print(f"[DEVICE] State set to: {'ON' if state else 'OFF'}")

# 🔧 TCP handler function
def handle_tcp_connection(conn, addr):
    ip, port = addr
    print(f"[TCP] Connected by {ip}:{port}")
    client_info["ip"] = ip
    client_info["port"] = port

    try:
        data = conn.recv(1024).decode().strip().lower()
        print(f"[TCP] Received: {data}")

        if data == "isready":
            conn.sendall(b"Device is ready")
        elif data == "turnon":
            set_device_state(True)
            conn.sendall(b"ACK - Turned ON")
        elif data == "turnoff":
            set_device_state(False)
            conn.sendall(b"ACK - Turned OFF")
        else:
            conn.sendall(b"Unknown command")
    except Exception as e:
        print(f"[TCP] Error: {e}")
    finally:
        conn.close()

# 🔧 Background TCP server thread
def tcp_server():
    HOST = "0.0.0.0"
    PORT = 4444
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((HOST, PORT))
        s.listen()
        print(f"[TCP] Listening on {HOST}:{PORT}")
        while True:
            conn, addr = s.accept()
            threading.Thread(target=handle_tcp_connection, args=(conn, addr), daemon=True).start()

# 🔧 Flask startup + background TCP
if __name__ == "__main__":
    threading.Thread(target=tcp_server, daemon=True).start()
    app.run(host="0.0.0.0", port=3333, debug=False)
