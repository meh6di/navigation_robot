# server.py (PC)
import socket, struct, pickle, cv2, numpy as np
import onnxruntime as ort

# --- MODEL SELECTION ---
# Swap between your custom model and the stock pretrained one by commenting/uncommenting:

MODEL_PATH = "/home/mehdi/PyCharmMiscProject/Github/navigation_robot/yolov8n.onnx"  # pretrained COCO model (sanity check)
# MODEL_PATH = "/home/mehdi/PyCharmMiscProject/Github/navigation_robot/yolo26.onnx"  # your custom model

session = ort.InferenceSession(MODEL_PATH, providers=["CPUExecutionProvider"])
input_name = session.get_inputs()[0].name
INPUT_SIZE = 640  # export size

for inp in session.get_inputs():
    print("INPUT:", inp.name, inp.shape, inp.type)
for out in session.get_outputs():
    print("OUTPUT:", out.name, out.shape, out.type)

# --- CLASS NAMES ---
# COCO has no "carton" class, so we only filter to "bottle" (COCO index 39).
# This dict maps the model's raw class_id -> the label we want to keep/show.
# Any class_id not in this dict gets skipped entirely.
ALLOWED_CLASSES = {
    39: "bottle",
}

# When you switch back to your custom model (which has both bottle + carton trained):
# ALLOWED_CLASSES = {0: "bottle", 1: "carton"}


def preprocess(frame):
    img = cv2.resize(frame, (INPUT_SIZE, INPUT_SIZE))
    img = img[:, :, ::-1].transpose(2, 0, 1).astype(np.float32) / 255.0
    return np.expand_dims(img, 0)


def postprocess(outputs, orig_w, orig_h, conf_thres=0.4):
    preds = outputs[0][0]  # shape (300, 6)
    detections = []

    scale_x = orig_w / 640.0
    scale_y = orig_h / 640.0

    for det in preds:
        x1, y1, x2, y2, conf, cls_id = det
        cls_id = int(cls_id)
        if conf < conf_thres:
            continue
        if cls_id not in ALLOWED_CLASSES:
            continue  # skip anything that's not bottle/carton
        detections.append({
            "class": ALLOWED_CLASSES[cls_id],
            "conf": float(conf),
            "box": [
                float(x1 * scale_x),
                float(y1 * scale_y),
                float(x2 * scale_x),
                float(y2 * scale_y)
            ]
        })
    return detections


def recv_msg(conn):
    raw_len = recvall(conn, 4)
    if not raw_len:
        return None
    msg_len = struct.unpack(">L", raw_len)[0]
    return recvall(conn, msg_len)


def recvall(conn, n):
    data = b""
    while len(data) < n:
        packet = conn.recv(n - len(data))
        if not packet:
            return None
        data += packet
    return data


def send_msg(conn, data):
    conn.sendall(struct.pack(">L", len(data)) + data)


HOST, PORT = "0.0.0.0", 9999
srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
srv.bind((HOST, PORT))
srv.listen(1)
print(f"Listening on {PORT}...")

conn, addr = srv.accept()
conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)  # lower latency, disable Nagle's algorithm
print("Connected:", addr)

while True:
    jpg_bytes = recv_msg(conn)
    if jpg_bytes is None:
        break
    frame = cv2.imdecode(np.frombuffer(jpg_bytes, np.uint8), cv2.IMREAD_COLOR)
    h, w = frame.shape[:2]

    input_tensor = preprocess(frame)
    outputs = session.run(None, {input_name: input_tensor})
    detections = postprocess(outputs, w, h)
    print(detections)  # debug: see what's being detected

    send_msg(conn, pickle.dumps(detections))

conn.close()