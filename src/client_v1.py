# client.py (Raspberry Pi)
import socket, struct, pickle, cv2, threading, time
from picamera2 import Picamera2
import pigpio

# ---------------- networking ----------------

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
sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
sock.connect((PC_IP, PC_PORT))

# ---------------- shared state ----------------

latest_result = {"detections": [], "error": None, "has_target": False}
lock = threading.Lock()

def receiver_thread():
    global latest_result
    while True:
        data = recv_msg(sock)
        if data is None:
            break
        with lock:
            latest_result = pickle.loads(data)

threading.Thread(target=receiver_thread, daemon=True).start()

# ---------------- ESC / motor setup ----------------
# RC-style ESCs, one-way (forward only), standard 1000-2000us pulses.
# Differential steering: bias one motor up and the other down around a
# base forward speed. Since motors can't reverse, the turn bias is clamped
# so neither pulse goes below MIN_US.

LEFT_ESC_PIN = 18
RIGHT_ESC_PIN = 19

MIN_US = 1000      # full stop / minimum throttle
MAX_US = 2000       # full throttle
NEUTRAL_US = 1000   # one-way ESC: "neutral" is stopped, not centered like a servo

BASE_SPEED_US = 1300        # forward cruise pulse width when driving straight
MAX_TURN_BIAS_US = 250      # max +/- adjustment applied to each side for steering
STOP_GRACE_PERIOD = 1.0     # seconds with no target before stopping

pi = pigpio.pi()
if not pi.connected:
    raise RuntimeError("Could not connect to pigpio daemon (is it running? `sudo pigpiod`)")

pi.set_servo_pulsewidth(LEFT_ESC_PIN, NEUTRAL_US)
pi.set_servo_pulsewidth(RIGHT_ESC_PIN, NEUTRAL_US)
time.sleep(1)  # let ESCs arm


def set_motors(left_us, right_us):
    left_us = max(MIN_US, min(MAX_US, left_us))
    right_us = max(MIN_US, min(MAX_US, right_us))
    pi.set_servo_pulsewidth(LEFT_ESC_PIN, left_us)
    pi.set_servo_pulsewidth(RIGHT_ESC_PIN, right_us)


def stop_motors():
    pi.set_servo_pulsewidth(LEFT_ESC_PIN, NEUTRAL_US)
    pi.set_servo_pulsewidth(RIGHT_ESC_PIN, NEUTRAL_US)


# ---------------- PID controller ----------------
# error is normalized in [-1, 1]: negative = target left of center, positive = right.
# Positive turn output -> steer right (slow right motor, speed up left motor), and vice versa.

class PID:
    def __init__(self, kp, ki, kd, output_limit):
        self.kp, self.ki, self.kd = kp, ki, kd
        self.output_limit = output_limit
        self.integral = 0.0
        self.prev_error = 0.0
        self.prev_time = time.time()

    def update(self, error):
        now = time.time()
        dt = max(1e-3, now - self.prev_time)
        self.prev_time = now

        self.integral += error * dt
        self.integral = max(-self.output_limit, min(self.output_limit, self.integral))  # anti-windup

        derivative = (error - self.prev_error) / dt
        self.prev_error = error

        output = self.kp * error + self.ki * self.integral + self.kd * derivative
        return max(-self.output_limit, min(self.output_limit, output))

    def reset(self):
        self.integral = 0.0
        self.prev_error = 0.0
        self.prev_time = time.time()


pid = PID(kp=180.0, ki=5.0, kd=40.0, output_limit=MAX_TURN_BIAS_US)

# ---------------- camera ----------------

picam2 = Picamera2()
picam2.configure(picam2.create_preview_configuration(main={"size": (640, 480)}))
picam2.start()

last_target_time = time.time()

try:
    while True:
        frame = picam2.capture_array()
        frame_bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)

        ok, jpg = cv2.imencode(".jpg", frame_bgr, [cv2.IMWRITE_JPEG_QUALITY, 80])
        send_msg(sock, jpg.tobytes())

        with lock:
            result = latest_result

        dets = result.get("detections", [])
        error = result.get("error")
        has_target = result.get("has_target", False)

        # draw detections
        for d in dets:
            x1, y1, x2, y2 = map(int, d["box"])
            cv2.rectangle(frame_bgr, (x1, y1), (x2, y2), (0, 255, 0), 2)
            cv2.putText(frame_bgr, f"{d['class']} {d['conf']:.2f}", (x1, y1 - 10),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)

        # navigation
        if has_target and error is not None:
            last_target_time = time.time()
            turn_bias = pid.update(error)
            # error > 0 means target is to the right -> speed up left, slow right
            left_us = BASE_SPEED_US + turn_bias
            right_us = BASE_SPEED_US - turn_bias
            set_motors(left_us, right_us)
            direction = "RIGHT" if turn_bias > 5 else ("LEFT" if turn_bias < -5 else "FORWARD")
            cv2.putText(frame_bgr, f"Direction: {direction}", (10, 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
        else:
            pid.reset()
            if time.time() - last_target_time > STOP_GRACE_PERIOD:
                stop_motors()
                cv2.putText(frame_bgr, "Direction: STOP", (10, 30),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)
            else:
                # brief dropout, keep going straight rather than stopping abruptly
                set_motors(BASE_SPEED_US, BASE_SPEED_US)
                cv2.putText(frame_bgr, "Direction: FORWARD (no target)", (10, 30),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)

        cv2.imshow("Detections", frame_bgr)
        if cv2.waitKey(1) == ord("q"):
            break
finally:
    stop_motors()
    pi.set_servo_pulsewidth(LEFT_ESC_PIN, 0)
    pi.set_servo_pulsewidth(RIGHT_ESC_PIN, 0)
    pi.stop()
    cv2.destroyAllWindows()
