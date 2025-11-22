import socket
import cv2
import numpy as np
import threading
import time

ip = "192.168.4.1"
port = 3333
output_filename = "output_video.avi"
frame_size = (1280, 720)
fps_estimate = 15

def get_data(ip, port, command):
    """Send a command and receive data via UDP."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(0.5)
    try:
        sock.sendto(command, (ip, port))
        data, _ = sock.recvfrom(65536)
        return data
    except socket.timeout:
        print("Socket timeout, no response received")
    finally:
        sock.close()


def receive_image(ip, port):
    """Receive and decode an image."""
    data = get_data(ip, port, b'get_camera')
    if data:
        np_array = np.frombuffer(data, dtype=np.uint8)
        img = cv2.imdecode(np_array, cv2.IMREAD_COLOR)
        if img is not None:
            return img
        else:
            print("Image could not be decoded")
    return None


def display_images(ip, port):
    """Continuously receive, display, and save images."""
    last_time = time.time()
    video_writer = None

    while True:
        image = receive_image(ip, port)
        current_time = time.time()
        dt = current_time - last_time
        last_time = current_time
        fps = 1.0 / dt if dt > 0 else 0
        print(f"UPS: {fps:.2f}")

        if image is not None:
            # Initialize VideoWriter if not already initialized
            if video_writer is None:
                height, width, _ = image.shape
                frame_size = (width, height)
                fourcc = cv2.VideoWriter_fourcc(*'MJPG')
                video_writer = cv2.VideoWriter(output_filename, fourcc, fps, frame_size)

            cv2.imshow("Camera Feed", image)
            video_writer.write(image)

        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

    if video_writer:
        video_writer.release()
    cv2.destroyAllWindows()


def main():
    display_images(ip, port)

if __name__ == "__main__":
    main()
