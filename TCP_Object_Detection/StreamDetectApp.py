
import socket
import struct
import threading
import queue
import time
import os
import yaml
import cv2
import numpy as np
from dataclasses import dataclass
from typing import Optional, Tuple, Any, List

# Optional: YOLO. If ultralytics isn't available, the app will still run without detection.
try:
    from ultralytics import YOLO
    YOLO_AVAILABLE = True
except Exception as e:
    YOLO_AVAILABLE = False
    YOLO_IMPORT_ERR = e

# Optional: GUI (Tkinter + PIL)
try:
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox
    from PIL import Image, ImageTk  # pillow
    TK_AVAILABLE = True
except Exception as e:
    TK_AVAILABLE = False
    TK_IMPORT_ERR = e


@dataclass
class AppConfig:
    # Networking
    tcp_host: str = "0.0.0.0"
    tcp_port: int = 8899
    # Model
    yolo_model_path: Optional[str] = None  # set dynamically if config yaml found
    imgsz: int = 640
    conf_thres: float = 0.4
    classes: float = 0.5
    # UI
    window_title: str = "StreamDetectApp"
    refresh_ms: int = 30  # ~33 fps
    # Paths
    save_dir: str = "captures"
    config_yaml: Optional[str] = None


class StreamDetectApp:
    """
    Class-based app that:
      - Listens for a TCP stream of JPEG frames: [u32 length][bytes...]
      - Shows the latest frame in a GUI window (Tkinter)
      - Can run YOLO detection on-demand or auto, and display annotated frames
    """
    def __init__(self, config: Optional[AppConfig] = None):
        self.cfg = config or AppConfig()
        # Load optional YAML config if provided or present in script's dir
        self._load_yaml_defaults()

        os.makedirs(self.cfg.save_dir, exist_ok=True)

        # Networking state
        self.sock: Optional[socket.socket] = None
        self.client_sock: Optional[socket.socket] = None
        self.net_thread: Optional[threading.Thread] = None
        self.stop_network = threading.Event()

        # Frame buffers
        self.latest_frame_lock = threading.Lock()
        self.latest_frame: Optional[np.ndarray] = None  # BGR
        self.annotated_frame: Optional[np.ndarray] = None  # BGR

        # YOLO state
        self.model = None
        self.yolo_loaded = False
        self.auto_detect = False
        self.detect_thread: Optional[threading.Thread] = None
        self.detect_running = threading.Event()

        # GUI state
        self.root: Optional["tk.Tk"] = None
        self.canvas: Optional["tk.Label"] = None
        self.status_var: Any = None
        self.auto_var: Any = None

        if TK_AVAILABLE:
            self._init_gui()
        else:
            print("[WARN] Tkinter/Pillow not available -> running in headless mode.")
            print("       Import error:", TK_IMPORT_ERR)

        if YOLO_AVAILABLE and self.cfg.yolo_model_path:
            self._load_yolo_model(self.cfg.yolo_model_path)

    # -----------------------
    # Config
    # -----------------------
    def _load_yaml_defaults(self):
        # If user provided a yaml path via cfg, use it; else try local config.yaml
        path = self.cfg.config_yaml or os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.yaml")
        if os.path.exists(path):
            try:
                with open(path, "r") as f:
                    y = yaml.safe_load(f) or {}
                # Adopt values if present
                self.cfg.yolo_model_path = y.get("yolo_model", self.cfg.yolo_model_path)
                self.cfg.imgsz = int(y.get("imgsz", self.cfg.imgsz))
                self.cfg.conf_thres = float(y.get("conf_thres", self.cfg.conf_thres))
                self.cfg.classes = list(y.get("classes", self.cfg.classes))
                host = y.get("tcp_host")
                port = y.get("tcp_port")
                if host: self.cfg.tcp_host = host
                if port: self.cfg.tcp_port = int(port)
                print(f"[CFG] Loaded YAML config from {path}")
            except Exception as e:
                print(f"[CFG] Failed to parse YAML {path}: {e}")
        else:
            print(f"[CFG] No YAML config found at {path}; using defaults.")

    # -----------------------
    # GUI
    # -----------------------
    def _init_gui(self):
        self.root = tk.Tk()
        self.root.title(self.cfg.window_title)

        # Top controls
        btn_frame = ttk.Frame(self.root, padding=8)
        btn_frame.pack(side=tk.TOP, fill=tk.X)

        ttk.Button(btn_frame, text="Start Stream", command=self.start_stream).pack(side=tk.LEFT, padx=4)
        ttk.Button(btn_frame, text="Stop Stream", command=self.stop_stream).pack(side=tk.LEFT, padx=4)
        ttk.Button(btn_frame, text="Detect Once", command=self.detect_once).pack(side=tk.LEFT, padx=4)

        self.auto_var = tk.BooleanVar(value=False)
        auto_cb = ttk.Checkbutton(btn_frame, text="Auto Detect", variable=self.auto_var, command=self._toggle_auto_detect)
        auto_cb.pack(side=tk.LEFT, padx=12)

        ttk.Button(btn_frame, text="Save Snapshot", command=self.save_snapshot).pack(side=tk.LEFT, padx=4)
        ttk.Button(btn_frame, text="Quit", command=self.quit).pack(side=tk.RIGHT, padx=4)

        # Canvas / Image area
        self.canvas = tk.Label(self.root)
        self.canvas.pack(side=tk.TOP, padx=8, pady=8)

        # Status bar
        self.status_var = tk.StringVar(value="Idle")
        status = ttk.Label(self.root, textvariable=self.status_var, relief=tk.SUNKEN, anchor=tk.W)
        status.pack(side=tk.BOTTOM, fill=tk.X)

        # Periodic refresh
        self.root.after(self.cfg.refresh_ms, self._refresh_loop)

    def _set_status(self, text: str):
        if self.status_var is not None:
            self.status_var.set(text)
        print("[STATUS]", text)

    def _toggle_auto_detect(self):
        self.auto_detect = self.auto_var.get() if self.auto_var is not None else False
        self._set_status(f"Auto Detect = {self.auto_detect}")
        if self.auto_detect:
            self._ensure_detect_thread_running()

    def _refresh_loop(self):
        # Called periodically to update the GUI image
        frame = None
        with self.latest_frame_lock:
            if self.annotated_frame is not None:
                frame = self.annotated_frame.copy()
            elif self.latest_frame is not None:
                frame = self.latest_frame.copy()

        if frame is not None and self.canvas is not None:
            # Convert BGR->RGB for Tkinter
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            img = Image.fromarray(rgb)
            imgtk = ImageTk.PhotoImage(image=img)
            self.canvas.imgtk = imgtk  # keep a ref
            self.canvas.configure(image=imgtk)

        # Re-schedule
        if self.root is not None:
            self.root.after(self.cfg.refresh_ms, self._refresh_loop)

    # -----------------------
    # Networking
    # -----------------------
    def start_stream(self):
        """Start a TCP server to receive frames from an ESP32 (or client)."""
        if self.net_thread and self.net_thread.is_alive():
            self._set_status("Stream already running.")
            return
        print("GGGG")
        self.stop_network.clear()
        self.net_thread = threading.Thread(target=self._tcp_server_loop, daemon=True)
        self.net_thread.start()
        self._set_status(f"Listening on {self.cfg.tcp_host}:{self.cfg.tcp_port}")

    def stop_stream(self):
        self.stop_network.set()
        try:
            if self.client_sock:
                self.client_sock.close()
        except:
            pass
        try:
            if self.sock:
                self.sock.close()
        except:
            pass
        self.sock = None
        self.client_sock = None
        self._set_status("Stream stopped.")

    def _tcp_server_loop(self):
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.sock.bind((self.cfg.tcp_host, self.cfg.tcp_port))
            self.sock.listen(1)
            self.sock.settimeout(1.0)  # so we can check stop flag
        except Exception as e:
            self._set_status(f"[NET] Failed to bind/listen: {e}")
            return

        self._set_status("[NET] Waiting for client...")
        while not self.stop_network.is_set():
            try:
                client, addr = self.sock.accept()
                self.client_sock = client
                self._set_status(f"[NET] Client connected: {addr}")
                client.settimeout(2.0)
                self._recv_frames_loop(client)
                self._set_status("[NET] Client disconnected.")
            except socket.timeout:
                continue
            except Exception as e:
                self._set_status(f"[NET] Accept error: {e}")
                time.sleep(0.5)

        self._set_status("[NET] Server loop exited.")

    def _recv_frames_loop(self, client: socket.socket):
        while not self.stop_network.is_set():
            # Read 4 bytes length
            try:
                raw_len = self._recvall(client, 4)
                if not raw_len:
                    break
                (frame_len,) = struct.unpack("!I", raw_len)
                if frame_len == 0 or frame_len > 10_000_000:
                    self._set_status(f"[NET] Bad frame length: {frame_len}")
                    break

                data = self._recvall(client, frame_len)
                if not data:
                    break

                # Decode JPEG
                arr = np.frombuffer(data, dtype=np.uint8)
                frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                if frame is None:
                    self._set_status("[NET] Failed to decode frame.")
                    continue

                with self.latest_frame_lock:
                    self.latest_frame = frame
                    # If auto detection is enabled, we set annotated_frame when detection thread runs
                    if not self.auto_detect:
                        self.annotated_frame = None
            except socket.timeout:
                continue
            except Exception as e:
                self._set_status(f"[NET] Receive error: {e}")
                break

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

    # -----------------------
    # Detection
    # -----------------------
    def _load_yolo_model(self, path: str):
        if not YOLO_AVAILABLE:
            self._set_status(f"[YOLO] ultralytics not available: {YOLO_IMPORT_ERR}")
            return
        try:
            self.model = YOLO(path)
            self.yolo_loaded = True
            self._set_status(f"[YOLO] Loaded model: {path}")
        except Exception as e:
            self._set_status(f"[YOLO] Failed to load model: {e}")
            self.yolo_loaded = False

    def _ensure_detect_thread_running(self):
        if self.detect_thread and self.detect_thread.is_alive():
            return
        self.detect_running.set()
        self.detect_thread = threading.Thread(target=self._detect_loop, daemon=True)
        self.detect_thread.start()

    def _detect_loop(self):
        self._set_status("[YOLO] Auto-detect loop started.")
        while self.detect_running.is_set():
            if not self.auto_detect or not self.yolo_loaded:
                time.sleep(0.05)
                continue
            frame = None
            with self.latest_frame_lock:
                if self.latest_frame is not None:
                    frame = self.latest_frame.copy()

            if frame is None:
                time.sleep(0.01)
                continue

            annotated = self._detect_on_frame(frame)
            with self.latest_frame_lock:
                self.annotated_frame = annotated
            # Yield to UI
            time.sleep(0.01)
        self._set_status("[YOLO] Auto-detect loop stopped.")

    def _detect_on_frame(self, frame: np.ndarray) -> np.ndarray:
        """Run YOLO detection on a single frame and return annotated frame."""
        if not self.yolo_loaded or self.model is None:
            return frame
        try:
            results = self.model.predict(source=frame, imgsz=self.cfg.imgsz, conf=self.cfg.conf_thres, classes=self.cfg.classes, verbose=False)
            # The ultralytics Results has .plot() to draw
            annotated = results[0].plot()
            print("Annotated shape:", annotated.shape)
            print("Detection results:", results[0].names, "  " ,results[0].boxes)
            return annotated
        except Exception as e:
            self._set_status(f"[YOLO] Predict error: {e}")
            return frame

    def detect_once(self):
        """Run detection once on the current frame."""
        frame = None
        with self.latest_frame_lock:
            if self.latest_frame is not None:
                frame = self.latest_frame.copy()
        if frame is None:
            self._set_status("[YOLO] No frame to detect.")
            return
        if not self.yolo_loaded:
            self._set_status("[YOLO] Model not loaded.")
            return

        annotated = self._detect_on_frame(frame)
        with self.latest_frame_lock:
            self.annotated_frame = annotated
        self._set_status("[YOLO] Detection complete.")

    # -----------------------
    # Utilities
    # -----------------------
    def save_snapshot(self):
        frame = None
        with self.latest_frame_lock:
            frame = self.annotated_frame if self.annotated_frame is not None else self.latest_frame
            if frame is not None:
                frame = frame.copy()
        if frame is None:
            self._set_status("[SAVE] No frame to save.")
            return
        ts = time.strftime("%Y%m%d_%H%M%S")
        path = os.path.join(self.cfg.save_dir, f"frame_{ts}.jpg")
        try:
            cv2.imwrite(path, frame)
            self._set_status(f"[SAVE] Saved snapshot: {path}")
        except Exception as e:
            self._set_status(f"[SAVE] Failed to save: {e}")

    def quit(self):
        # Stop threads and close sockets
        self.auto_detect = False
        self.detect_running.clear()
        self.stop_stream()
        if self.root is not None:
            self.root.destroy()

    # -----------------------
    # Public entry
    # -----------------------
    def run(self):
        if self.root is None:
            print("[INFO] GUI not available. Use start_stream() and keyboard loop with OpenCV if desired.")
            # Fallback headless loop: show frames with cv2.imshow (if available) or just receive.
            self.start_stream()
            try:
                while True:
                    time.sleep(0.5)
            except KeyboardInterrupt:
                pass
            finally:
                self.quit()
        else:
            self.root.protocol("WM_DELETE_WINDOW", self.quit)
            self.root.mainloop()


if __name__ == "__main__":
    app = StreamDetectApp()
    app.run()
