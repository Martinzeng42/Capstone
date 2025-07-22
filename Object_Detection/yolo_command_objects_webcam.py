import cv2
import asyncio
import threading
from ultralytics import YOLO
import math

model = YOLO("Object_Detection/yolo11n.pt")

cap = cv2.VideoCapture(0)  # webcam

# Command mappings for each object type
# Move this to a separate file eventually
OBJECT_COMMANDS = {
    "bottle": {
        "1": "Open bottle",
        "2": "Close bottle",
        "3": "Pour liquid"
    },
    "cell phone": {
        "1": "Call contact",
        "2": "Send SMS"
    },
    "laptop": {
        "1": "Open browser",
        "2": "Shutdown laptop",
        "3": "Play video",
        "4": "Mute audio"
    },
    "keyboard": {
        "1": "Type hello",
        "2": "Enable caps lock"
    },
    "scissors": {
        "1": "Cut paper"
    }
}

active_object = None
active_box_coords = None  # store bounding box of active object

# Async command sender
async def send_command_async(object_name: str, command: str):
    print(f"Sending command to {object_name}: {command}")
    await asyncio.sleep(0.2)  # simulate delay
    print(f"✅ Command '{command}' sent to {object_name}")

def run_asyncio_task(coro):
    threading.Thread(target=lambda: asyncio.run(coro)).start()

while True:
    ret, frame = cap.read()
    if not ret:
        break

    height, width = frame.shape[:2]
    frame_center = (width // 2, height // 2)

    # Run YOLO detection for selected classes
    results = model.predict(     # model and model.predict both work
        frame, 
        classes=[39, 63, 66, 67, 76],    # 39: bottle, 63: laptop, 66: keyboard, 67: cell phone, 76: scissors
        verbose=False,    # turns off the prints that get spammed every few milliseconds, turn it on to see how long inference takes
        conf=0.4,    # minimum confidence threshold, default is 0.25
        )

    closest_obj = None
    closest_box = None
    closest_distance = float("inf")

    all_detections = []  # store all detections for drawing

    for r in results:
        for box in r.boxes:
            cls = int(box.cls[0])
            label = r.names[cls]
            conf = float(box.conf[0])
            x1, y1, x2, y2 = map(int, box.xyxy[0])

            # Object center
            obj_center_x = (x1 + x2) // 2
            obj_center_y = (y1 + y2) // 2
            dist = math.dist((obj_center_x, obj_center_y), frame_center)

            all_detections.append((label, conf, (x1, y1, x2, y2), dist))

            # Keep closest one
            if dist < closest_distance:
                closest_distance = dist
                closest_obj = label
                closest_box = (x1, y1, x2, y2)

    # Update active object
    active_object = closest_obj
    active_box_coords = closest_box

    # Draw all detections
    for (label, conf, (x1, y1, x2, y2), dist) in all_detections:
        color = (0, 255, 0)  # default green
        thickness = 2

        # If this is the active one, highlight in blue
        if (label == active_object) and (x1, y1, x2, y2) == active_box_coords:
            color = (255, 0, 0)  # blue
            thickness = 3

        cv2.rectangle(frame, (x1, y1), (x2, y2), color, thickness)
        cv2.putText(frame, f"{label} {conf:.2f}", (x1, y1 - 10),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 2)

    # Draw frame center (red dot)
    cv2.circle(frame, frame_center, 5, (0, 0, 255), -1)

    # Show currently active object & available commands
    if active_object:
        cmds = OBJECT_COMMANDS.get(active_object, {})
        cv2.putText(frame, f"Active: {active_object} -> {', '.join([f'{k}:{v}' for k,v in cmds.items()])}",
                    (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 0), 2)
    else:
        cv2.putText(frame, "No active object", (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)

    cv2.imshow("YOLO Detection", frame)

    # Key handling
    key = cv2.waitKey(1) & 0xFF
    if key == 27:  # ESC quits
        break

    # Only active object responds to number keys
    if active_object and key in range(ord('1'), ord('9') + 1):
        key_str = chr(key)
        cmds_for_obj = OBJECT_COMMANDS.get(active_object, {})
        if key_str in cmds_for_obj:
            cmd = cmds_for_obj[key_str]
            run_asyncio_task(send_command_async(active_object, cmd))

cap.release()
cv2.destroyAllWindows()
