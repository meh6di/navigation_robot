# client.py (Raspberry Pi)
# Autonomous version: sends camera frames to server.py over TCP, receives
# back a list of detected bottle bounding boxes, and drives the motors
# to center the nearest priority target in frame and approach it.

import socket, struct, pickle, cv2, threading, time, math
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


PC_IP = "172.26.183.244"
PC_PORT = 9999

sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
sock.connect((PC_IP, PC_PORT))

# ---------------- shared state ----------------
latest_result = []  
result_lock = threading.Lock()

latest_jpeg = None
jpeg_lock = threading.Lock()


def network_thread():
    global latest_result, latest_jpeg
    while True:
        with jpeg_lock:
            jpg_data = latest_jpeg
            latest_jpeg = None 

        if jpg_data is None:
            time.sleep(0.01)
            continue

        try:
            send_msg(sock, jpg_data)
        except:
            print("Network dropped.")
            break

        data = recv_msg(sock)
        if data is None:
            break

        with result_lock:
            latest_result = pickle.loads(data)

threading.Thread(target=network_thread, daemon=True).start()

# ---------------- video recording ----------------

OUTPUT_PATH = "autonomous_run.mp4"
FRAME_SIZE = (640, 480)
FPS = 30
FRAME_SKIP = 3

# ---------------- ESC / motor setup ----------------

LEFT_ESC_PIN = 13
RIGHT_ESC_PIN = 12

MIN_US = 950       
MAX_US = 2000       
NEUTRAL_US = 950 

LEFT_MOTOR_FORWARD = 1050   
RIGHT_MOTOR_FORWARD = 1050   
TURN_SPEED = 1050           

# Navigation tuning
CENTER_TOLERANCE = 0.1      
GIVEUP_TIMEOUT = 3
STOP_DELAY = 0.15 
BURST_TURN_ON = 0.15   # Time to turn motors on during a burst (seconds)
BURST_TURN_OFF = 0.20  # Time to pause motors after a burst (seconds)

pi = pigpio.pi()
if not pi.connected:
    raise RuntimeError("Could not connect to pigpio daemon (is it running? `sudo pigpiod`)")

pi.set_servo_pulsewidth(LEFT_ESC_PIN, NEUTRAL_US)
pi.set_servo_pulsewidth(RIGHT_ESC_PIN, NEUTRAL_US)
time.sleep(1)  


def set_motors(left_us, right_us):
    left_us = max(MIN_US, min(MAX_US, left_us))
    right_us = max(MIN_US, min(MAX_US, right_us))
    pi.set_servo_pulsewidth(LEFT_ESC_PIN, left_us)
    pi.set_servo_pulsewidth(RIGHT_ESC_PIN, right_us)


def stop_motors():
    pi.set_servo_pulsewidth(LEFT_ESC_PIN, NEUTRAL_US)
    pi.set_servo_pulsewidth(RIGHT_ESC_PIN, NEUTRAL_US)


def go_forward():
    set_motors(LEFT_MOTOR_FORWARD, RIGHT_MOTOR_FORWARD)


def turn_left():
    set_motors(NEUTRAL_US, TURN_SPEED)


def turn_right():
    set_motors(TURN_SPEED, NEUTRAL_US)


# ---------------- camera ----------------

picam2 = Picamera2()
picam2.configure(picam2.create_preview_configuration(main={"size": FRAME_SIZE}))
picam2.start()

WINDOW_NAME = "Autonomous Run"
cv2.namedWindow(WINDOW_NAME)

fourcc = cv2.VideoWriter_fourcc(*"mp4v")
writer = cv2.VideoWriter(OUTPUT_PATH, fourcc, FPS, FRAME_SIZE)
if not writer.isOpened():
    raise RuntimeError(f"Could not open video writer for {OUTPUT_PATH}")

frame_w, frame_h = FRAME_SIZE
frame_center_x = frame_w / 2.0
frame_center_y = frame_h / 2.0

last_direction = "STOP"
last_seen_time = time.time()

# ---------------- Burst State Tracking ----------------
burst_state = "ON"
burst_start_time = time.time()

try:
    while True:

        frame = picam2.capture_array()
        frame_bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
        writer.write(frame_bgr)
        
        ok, jpg = cv2.imencode(".jpg", frame_bgr, [cv2.IMWRITE_JPEG_QUALITY, 50])
        if ok:
            with jpeg_lock:
                latest_jpeg = jpg.tobytes()

        with result_lock:
            targets = latest_result

        box = None
        conf = None
        now = time.time()

        display_text = "STOP"
        left_us = NEUTRAL_US
        right_us = NEUTRAL_US

        if targets:
            bottom_targets = []
            regular_targets = []

            for t in targets:
                t_box = t.get("box")
                t_conf = t.get("conf")
                
                if t_box:
                    x1, y1, x2, y2 = t_box
                    box_center_x = (x1 + x2) / 2.0
                    box_center_y = (y1 + y2) / 2.0
                    
                    cv2.rectangle(frame_bgr, (int(x1), int(y1)), (int(x2), int(y2)), (0, 255, 255), 2)
                    cv2.putText(frame_bgr, f"Seen {t_conf:.2f}", (int(x1), max(20, int(y1) - 10)),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)

                    if box_center_y > frame_center_y:
                        bottom_targets.append(t)
                    else:
                        regular_targets.append(t)

            best_target = None

            if bottom_targets:
                best_target = max(bottom_targets, key=lambda t: abs(frame_center_y - ((t["box"][1] + t["box"][3]) / 2.0)))
            elif regular_targets:
                best_target = min(regular_targets, key=lambda t: abs(frame_center_y - ((t["box"][1] + t["box"][3]) / 2.0)))

            if best_target:
                box = best_target["box"]
                conf = best_target["conf"]

        # Act on the prioritized target
        if box is not None:
            last_seen_time = now
            x1, y1, x2, y2 = box
            box_center_x = (x1 + x2) / 2.0
            offset = (box_center_x - frame_center_x) / frame_w  

            if abs(offset) <= CENTER_TOLERANCE:
                new_direction = "FORWARD"
            elif offset < 0:
                new_direction = "LEFT"
            else:
                new_direction = "RIGHT"

            if new_direction != last_direction and last_direction != "STOP":
                stop_motors()
                time.sleep(STOP_DELAY)

            # Instantly start a new burst if we are beginning a new turn
            if new_direction in ["LEFT", "RIGHT"] and last_direction != new_direction:
                burst_state = "ON"
                burst_start_time = now

            if new_direction == "FORWARD":
                go_forward()
                left_us, right_us = LEFT_MOTOR_FORWARD, RIGHT_MOTOR_FORWARD
                display_text = "FORWARD"
                
            elif new_direction in ["LEFT", "RIGHT"]:
                
                # Check how much time has passed in the current burst phase
                elapsed_burst = now - burst_start_time
                if burst_state == "ON" and elapsed_burst >= BURST_TURN_ON:
                    burst_state = "OFF"
                    burst_start_time = now
                elif burst_state == "OFF" and elapsed_burst >= BURST_TURN_OFF:
                    burst_state = "ON"
                    burst_start_time = now

                # Apply motor outputs
                if new_direction == "LEFT":
                    if burst_state == "ON":
                        turn_left()
                        left_us, right_us = NEUTRAL_US, TURN_SPEED
                        display_text = "LEFT (BURST ON)"
                    else:
                        stop_motors()
                        left_us, right_us = NEUTRAL_US, NEUTRAL_US
                        display_text = "LEFT (PAUSED)"
                        
                elif new_direction == "RIGHT":
                    if burst_state == "ON":
                        turn_right()
                        left_us, right_us = TURN_SPEED, NEUTRAL_US
                        display_text = "RIGHT (BURST ON)"
                    else:
                        stop_motors()
                        left_us, right_us = NEUTRAL_US, NEUTRAL_US
                        display_text = "RIGHT (PAUSED)"

            last_direction = new_direction

            cv2.rectangle(frame_bgr, (int(x1), int(y1)), (int(x2), int(y2)), (0, 255, 0), 4)
            cv2.putText(frame_bgr, f"TARGET {conf:.2f}", (int(x1), max(20, int(y1) - 10)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)

        else:
            time_since_seen = now - last_seen_time

            if time_since_seen <= GIVEUP_TIMEOUT:
                if last_direction == "FORWARD":
                    go_forward()
                    left_us, right_us = LEFT_MOTOR_FORWARD, RIGHT_MOTOR_FORWARD
                    display_text = "COASTING (FORWARD)"
                    
                elif last_direction in ["LEFT", "RIGHT"]:
                    
                    # Keep the burst timing running while coasting
                    elapsed_burst = now - burst_start_time
                    if burst_state == "ON" and elapsed_burst >= BURST_TURN_ON:
                        burst_state = "OFF"
                        burst_start_time = now
                    elif burst_state == "OFF" and elapsed_burst >= BURST_TURN_OFF:
                        burst_state = "ON"
                        burst_start_time = now
                        
                    if last_direction == "LEFT":
                        if burst_state == "ON":
                            turn_left()
                            left_us, right_us = NEUTRAL_US, TURN_SPEED
                            display_text = "COASTING (LEFT BURST)"
                        else:
                            stop_motors()
                            left_us, right_us = NEUTRAL_US, NEUTRAL_US
                            display_text = "COASTING (LEFT PAUSED)"
                            
                    elif last_direction == "RIGHT":
                        if burst_state == "ON":
                            turn_right()
                            left_us, right_us = TURN_SPEED, NEUTRAL_US
                            display_text = "COASTING (RIGHT BURST)"
                        else:
                            stop_motors()
                            left_us, right_us = NEUTRAL_US, NEUTRAL_US
                            display_text = "COASTING (RIGHT PAUSED)"
                else:
                    stop_motors()
                    display_text = "COASTING (STOP)"
            else:
                if last_direction != "STOP":
                    stop_motors()
                    last_direction = "STOP"
                
                display_text = "STOP (Timeout)"

        cv2.putText(frame_bgr, f"Action: {display_text}", (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
        cv2.putText(frame_bgr, f"Left PWM:  {int(left_us)} us", (10, 60),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 0), 2)
        cv2.putText(frame_bgr, f"Right PWM: {int(right_us)} us", (10, 90),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 0), 2)

        cv2.imshow(WINDOW_NAME, frame_bgr)

        if cv2.waitKey(1) & 0xFF == ord("q"):
            break

except KeyboardInterrupt:
    print("\nKeyboardInterrupt received — stopping motors safely.")

finally:
    stop_motors()
    pi.set_servo_pulsewidth(LEFT_ESC_PIN, 0)
    pi.set_servo_pulsewidth(RIGHT_ESC_PIN, 0)
    pi.stop()
    writer.release()
    cv2.destroyAllWindows()
    sock.close()
    print(f"Video saved to {OUTPUT_PATH}")