
import socket
import struct
import threading
import time
import os
import yaml
import cv2
import numpy as np
from typing import Optional
import queue
from collections import Counter

# ---------- Optional YOLO ----------
try:
    from ultralytics import YOLO
    YOLO_AVAILABLE = True
except Exception as e:
    YOLO_AVAILABLE = False
    YOLO_IMPORT_ERR = e


class StreamYOLOTCPServer:
    """
    Server-only (bind/listen) OpenCV viewer.
    Your Mac binds to host:port and waits for an ESP32 client to connect and stream
    frames as [4-byte length][JPEG bytes]. Replaces Flask routes with keys:
        s=start (bind+listen), x=stop, d=detect-once, a=auto toggle, w=save, q=quit

    Config (config.yaml, next to script):
      bind_host: "0.0.0.0"
      bind_port: 12345
      length_unpack: "!I"      # use "!I" if ESP32 sends htonl(frameLen); "<I" if native little-endian
      window_title: "YOLO TCP Server"
      refresh_delay_ms: 1
      yolo_model: null         # or "yolo11n.pt"
      imgsz: 640
      conf: 0.4
      classes: 0.5
      snapshot_dir: "captures"
      hud: true
    """

    def __init__(self, cmd_queue = None, cam_data_queue = None, cfg_path: Optional[str] = None):
        self.cfg = {
            "tcp_host": "0.0.0.0",
            "tcp_port": 12345,
            "length_unpack": "!I",      # default to network order; switch to "<I" if ESP32 sends native
            "window_title": "YOLO TCP Server",
            "refresh_delay_ms": 1,
            "yolo_model": None,
            "imgsz": 640,
            "conf": 0.4,
            "classes": None,
            "snapshot_dir": "captures",
            "hud": True,
        }
        self._load_yaml(cfg_path)
        os.makedirs(self.cfg["snapshot_dir"], exist_ok=True)

        # Networking
        self.sock = None
        self.client_sock = None
        self.net_thread = None
        self.stop_net = threading.Event()

        # Thread to thread communication
        self.cmd_queue = cmd_queue
        self.cam_data_queue = cam_data_queue

        # Frames
        self.lock = threading.Lock()
        self.latest = None
        self.annotated = None

        # YOLO
        self.model = None
        self.yolo_ready = False
        self.auto = False
        self.det_thread = None
        self.det_run = threading.Event()

        if YOLO_AVAILABLE and self.cfg.get("yolo_model"):
            try:
                self.model = YOLO(self.cfg["yolo_model"])
                self.yolo_ready = True
                print("[YOLO] Loaded:", self.cfg["yolo_model"])
            except Exception as e:
                print("[YOLO] Load error:", e)

        self._status = "Idle"
        self.quit_state = False

    # -------------- Config --------------
    def _load_yaml(self, path: Optional[str]):
        if path is None:
            path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.yaml")
        if os.path.exists(path):
            try:
                with open(path, "r") as f:
                    y = yaml.safe_load(f) or {}
                self.cfg.update(y)
                print(f"[CFG] Loaded YAML from {path}")
            except Exception as e:
                print(f"[CFG] Failed to parse YAML {path}: {e}")

    # -------------- Status/HUD --------------
    def _set_status(self, s: str):
        self._status = s
        print("[STATUS]", s)

    def _draw_hud(self, img: np.ndarray) -> np.ndarray:
        if not self.cfg.get("hud", True):
            return img
        hud = img.copy()
        h, w = hud.shape[:2]
        text1 = f"{self._status} | Auto:{self.auto} | YOLO:{'OK' if self.yolo_ready else 'OFF'}"
        cv2.rectangle(hud, (0, 0), (w, 30), (0, 0, 0), thickness=-1)
        cv2.putText(hud, text1, (10, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 1, cv2.LINE_AA)
        return hud

    # -------------- Capture 5 frames --------------
    def _capture_detect_5(self):
        """Capture 5 frames and run detection on each."""
        frames = []
        for _ in range(5):
            with self.lock:
                if self.latest is not None:
                    frames.append(self.latest.copy())
            time.sleep(0.05)  # small delay between frames
        
        # Yolo
        all_detections = []
        for frame in frames:
            results = self.model.predict(source=frame, imgsz=self.cfg["imgsz"],
                                         conf=self.cfg["conf"], classes=self.cfg["classes"], verbose=False)
            # write detected objects to global variable
            r = results[0]
            # Get detected object names
            detected_names = [r.names[int(cls)] for cls in r.boxes.cls]
            if detected_names:
                all_detections.extend(detected_names)

        if all_detections:
            most_common = Counter(all_detections).most_common(1)[0][0]
            print("Most common object:", most_common)
            print("Data type of most_common:", type(most_common))
            self.cam_data_queue.put(most_common)
        else:
            print("No objects detected in any frame.")
            self.cam_data_queue.put("EMPTY")

    # -------------- Public actions --------------
    def start(self):
        """Bind/listen in a background thread."""
        self._set_status("Started.")
        if self.net_thread and self.net_thread.is_alive():
            self._set_status("Already listening.")
            return
        self.stop_net.clear()
        self.net_thread = threading.Thread(target=self._server_loop, daemon=True)
        self.net_thread.start()

    def stop(self):
        self.stop_net.set()
        for s in (self.client_sock, self.sock):
            if s:
                try: s.close()
                except: pass
        self.sock = None
        self.client_sock = None
        self._set_status("Stopped.")

    def detect_once(self):
        if not self.yolo_ready:
            self._set_status("[YOLO] Model not loaded.")
            return
        frame = None
        with self.lock:
            if self.latest is not None:
                frame = self.latest.copy()
        if frame is None:
            self._set_status("[YOLO] No frame."); return
        try:
            res = self.model.predict(source=frame, imgsz=self.cfg["imgsz"],
                                     conf=self.cfg["conf"], classes=self.cfg["classes"], verbose=False)
            anno = res[0].plot()
            with self.lock:
                self.annotated = anno
            self._set_status("[YOLO] Detection complete.")
        except Exception as e:
            self._set_status(f"[YOLO] Predict error: {e}")

    def toggle_auto(self):
        self.auto = not self.auto
        self._set_status(f"Auto Detect = {self.auto}")
        if self.auto and (not self.det_thread or not self.det_thread.is_alive()):
            self.det_run.set()
            self.det_thread = threading.Thread(target=self._auto_loop, daemon=True)
            self.det_thread.start()
        if not self.auto:
            self.det_run.clear()

    def save_snapshot(self):
        frame = None
        with self.lock:
            frame = self.annotated if self.annotated is not None else self.latest
            if frame is not None:
                frame = frame.copy()
        if frame is None:
            self._set_status("[SAVE] No frame."); return
        ts = time.strftime("%Y%m%d_%H%M%S")
        path = os.path.join(self.cfg["snapshot_dir"], f"frame_{ts}.jpg")
        try:
            cv2.imwrite(path, frame); self._set_status(f"[SAVE] {path}")
        except Exception as e:
            self._set_status(f"[SAVE] Error: {e}")

    # -------------- Auto-detect thread --------------
    def _auto_loop(self):
        self._set_status("[YOLO] Auto loop started.")
        while self.det_run.is_set():
            if not self.auto or not self.yolo_ready:
                time.sleep(0.02); continue
            with self.lock:
                frm = self.latest.copy() if self.latest is not None else None
            if frm is None:
                time.sleep(0.01); continue
            try:
                res = self.model.predict(source=frm, imgsz=self.cfg["imgsz"],
                                         conf=self.cfg["conf"], classes=self.cfg["classes"], verbose=False)
                anno = res[0].plot()
                with self.lock:
                    self.annotated = anno
            except Exception as e:
                self._set_status(f"[YOLO] Predict error: {e}")
                time.sleep(0.05)
        self._set_status("[YOLO] Auto loop stopped.")

    # -------------- Networking (server) --------------
    def _server_loop(self):
        host = self.cfg["tcp_host"]
        port = int(self.cfg["tcp_port"])
        fmt = self.cfg.get("length_unpack", "!I")

        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.sock.bind((host, port))
            self.sock.listen(1)
            self.sock.settimeout(1.0)
        except Exception as e:
            self._set_status(f"[NET] Bind/listen error: {e}")
            return

        self._set_status(f"[NET] Server listening on {host}:{port}")
        while not self.stop_net.is_set():
            try:
                client, addr = self.sock.accept()
                self.client_sock = client
                self._set_status(f"[NET] Client connected: {addr}")
                client.settimeout(2.0)

                while not self.stop_net.is_set():
                    raw = self._recvall(client, 4)
                    if not raw: break
                    (n,) = struct.unpack(fmt, raw)
                    if n <= 0 or n > 10_000_000:
                        self._set_status(f"[NET] Bad length: {n}"); break
                    data = self._recvall(client, n)
                    if not data: break
                    arr = np.frombuffer(data, dtype=np.uint8)
                    frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                    if frame is None:
                        self._set_status("[NET] Decode failed."); continue
                    with self.lock:
                        self.latest = frame
                        self.annotated = None

                self._set_status("[NET] Client disconnected.")
                try: client.close()
                except: pass

            except socket.timeout:
                continue
            except Exception as e:
                self._set_status(f"[NET] Accept/recv error: {e}")
                time.sleep(0.2)

        self._set_status("[NET] Server loop exit.")

    @staticmethod
    def _recvall(sock: socket.socket, n: int) -> Optional[bytes]:
        buf = bytearray()
        while len(buf) < n:
            try:
                chunk = sock.recv(n - len(buf))
            except socket.timeout:
                continue
            if not chunk:
                return None
            buf.extend(chunk)
        return bytes(buf)


    # -------------- Main loop (imshow) --------------
    def run(self, headless=True):
        if headless:
            print("[INFO] Running in headless mode. No GUI will be displayed.")
            try:
                while not self.quit_state:
                    cmd = None
                    try:
                        cmd = self.cmd_queue.get(timeout=0.1)
                    except queue.Empty:
                        pass
                    if cmd == "start_stream":
                        self._set_status("[CMD] Start stream received")
                        self.start()
                        time.sleep(2)
                        self._capture_detect_5()
                        self.stop()
            finally:
                self.stop()
                self.det_run.clear()
                self._set_status("Exited.")
                time.sleep(0.05)

        else:
            win = self.cfg.get("window_title", "YOLO TCP Server")
            delay = max(1, int(self.cfg.get("refresh_delay_ms", 1)))
            cv2.namedWindow(win, cv2.WINDOW_NORMAL)

            print("""
            ================= Controls =================
            Keys in the OpenCV window (focus the window first):
            s = Start (bind + listen)
            x = Stop
            d = Detect once
            a = Toggle auto detect
            w = Save snapshot
            q = Quit
            ===========================================
            """)

            try:
                while True:
                    # check for commands
                    try:
                        cmd = self.cmd_queue.get_nowait()
                        if cmd == "start_stream":
                            self.start()
                            self._capture_detect_5()
                            self.stop()
                    except queue.Empty:
                        pass

                    with self.lock:
                        frame = self.annotated if self.annotated is not None else self.latest
                        frame = frame.copy() if frame is not None else None

                    if frame is None:
                        blank = np.zeros((240, 320, 3), dtype=np.uint8)
                        hud = self._draw_hud(blank)
                    else:
                        hud = self._draw_hud(frame)

                    cv2.imshow(win, hud)
                    k = cv2.waitKey(delay) & 0xFF
                    if k == ord('q'):
                        break
                    elif k == ord('s'):
                        self.start()
                    elif k == ord('x'):
                        self.stop()
                    elif k == ord('d'):
                        self.detect_once()
                    elif k == ord('a'):
                        self.toggle_auto()
                    elif k == ord('w'):
                        self.save_snapshot()

            finally:
                try: cv2.destroyWindow(win)
                except: pass
                self.stop()
                self.det_run.clear()
                self._set_status("Exited.")
                time.sleep(0.05)


# if __name__ == "__main__":
#     app = StreamYOLOTCPServer()
#     app.run()
