# client.py (Raspberry Pi)
import socket, struct, pickle, cv2, threading, queue
from picamera2 import Picamera2

def send_msg(conn, data):
    conn.sendall(struct.pack(">L", len(data)) + data)

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

PC_IP = "192.168.1.100"
PC_PORT = 9999

sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)  # disable Nagle's algorithm, cuts latency
sock.connect((PC_IP, PC_PORT))

latest_detections = []
lock = threading.Lock()

def receiver_thread():
    global latest_detections
    while True:
        data = recv_msg(sock)
        if data is None:
            break
        with lock:
            latest_detections = pickle.loads(data)

threading.Thread(target=receiver_thread, daemon=True).start()

picam2 = Picamera2()
picam2.configure(picam2.create_preview_configuration(main={"size": (640, 480)}))
picam2.start()

while True:
    frame = picam2.capture_array()
    frame_bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)

    ok, jpg = cv2.imencode(".jpg", frame_bgr, [cv2.IMWRITE_JPEG_QUALITY, 80])
    send_msg(sock, jpg.tobytes())  # fire and forget, don't wait

    with lock:
        dets = latest_detections

    for d in dets:
        x1, y1, x2, y2 = map(int, d["box"])
        cv2.rectangle(frame_bgr, (x1, y1), (x2, y2), (0, 255, 0), 2)
        cv2.putText(frame_bgr, f"{d['class']} {d['conf']:.2f}", (x1, y1-10),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)

    cv2.imshow("Detections", frame_bgr)
    if cv2.waitKey(1) == ord("q"):
        break