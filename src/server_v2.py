# server.py (PC) — runs detection with best_smith.pt and computes steering error
import socket, struct, pickle, cv2, numpy as np
from ultralytics import YOLO

# --- MODEL ---
MODEL_PATH = "best_smith.pt"  # your custom trained model
model = YOLO(MODEL_PATH)

CLASS_NAMES = ["bottle", "plastic", "paper"]  # must match training order
CONF_THRES = 0.5

TOLERANCE_PX_FRACTION = 0.03  # ~3% of frame width counts as "centered" -> no turn needed


def calculate_distance(p1, p2):
    return ((p1[0] - p2[0]) ** 2 + (p1[1] - p2[1]) ** 2) ** 0.5


def run_detection(frame):
    """Run YOLO on a frame, return detections + steering error for the nearest target."""
    h, w = frame.shape[:2]
    frame_center = (w // 2, h // 2)
    tolerance_px = w * TOLERANCE_PX_FRACTION

    results = model.predict(source=frame, conf=CONF_THRES, verbose=False)

    detections = []
    nearest_distance = float("inf")
    nearest_center = None

    for r in results:
        for box in r.boxes:
            x1, y1, x2, y2 = box.xyxy[0].tolist()
            cls_id = int(box.cls[0])
            conf = float(box.conf[0])
            cls_name = CLASS_NAMES[cls_id] if cls_id < len(CLASS_NAMES) else str(cls_id)

            x_center = (x1 + x2) / 2
            y_center = (y1 + y2) / 2

            detections.append({
                "class": cls_name,
                "conf": conf,
                "box": [x1, y1, x2, y2],
            })

            dist = calculate_distance(frame_center, (x_center, y_center))
            if dist < nearest_distance:
                nearest_distance = dist
                nearest_center = (x_center, y_center)

    # Steering error: normalized horizontal offset of nearest target from center.
    # Negative = target is left of center, positive = right. None = nothing detected.
    error = None
    has_target = nearest_center is not None
    if has_target:
        deviation_x = nearest_center[0] - frame_center[0]
        if abs(deviation_x) <= tolerance_px:
            error = 0.0
        else:
            error = deviation_x / (w / 2)  # normalized to [-1, 1]

    return {
        "detections": detections,
        "error": error,          # None if nothing detected, else float in [-1, 1]
        "has_target": has_target,
    }


def recvall(conn, n):
    data = b""
    while len(data) < n:
        packet = conn.recv(n - len(data))
        if not packet:
            return None
        data += packet
    return data


def recv_msg(conn):
    raw_len = recvall(conn, 4)
    if not raw_len:
        return None
    msg_len = struct.unpack(">L", raw_len)[0]
    return recvall(conn, msg_len)


def send_msg(conn, data):
    conn.sendall(struct.pack(">L", len(data)) + data)


HOST, PORT = "0.0.0.0", 9999
srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
srv.bind((HOST, PORT))
srv.listen(1)
print(f"Listening on {PORT}...")

conn, addr = srv.accept()
conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
print("Connected:", addr)

try:
    while True:
        jpg_bytes = recv_msg(conn)
        if jpg_bytes is None:
            break
        frame = cv2.imdecode(np.frombuffer(jpg_bytes, np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            continue

        result = run_detection(frame)
        print(result["detections"], "error:", result["error"])  # debug

        send_msg(conn, pickle.dumps(result))
finally:
    conn.close()
