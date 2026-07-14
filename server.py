# server.py (PC)
import socket, struct, pickle, cv2, numpy as np
from ultralytics import YOLO
MODEL_PATH = "bottle_weights_openvino_model"
CONFIDENCE_THRESHOLD = 0.6

# Load the model
model = YOLO(MODEL_PATH, task="detect")

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

while True:
    # 1. Receive the absolutely freshest frame from the Pi
    jpg_bytes = recv_msg(conn)
    if jpg_bytes is None:
        break

    frame = cv2.imdecode(np.frombuffer(jpg_bytes, np.uint8), cv2.IMREAD_COLOR)

    # 2. Run inference immediately
    results = model(frame, conf=CONFIDENCE_THRESHOLD, verbose=False)

    targets = []

    # 3. Collect ALL detected targets
    for result in results:
        for box in result.boxes:
            conf = float(box.conf[0])
            x1, y1, x2, y2 = map(float, box.xyxy[0])
            targets.append({
                "box": [x1, y1, x2, y2],
                "conf": conf
            })

    # 4. Send the list back to the Pi
    send_msg(conn, pickle.dumps(targets))

conn.close()