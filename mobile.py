import socket
import cv2
import numpy as np
import threading
import time

from kivy.app import App
from kivy.uix.image import Image
from kivy.clock import Clock
from kivy.graphics.texture import Texture
from kivy.uix.boxlayout import BoxLayout

ip = "192.168.4.1"
port = 3333

def get_data(ip, port, command):
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
    data = get_data(ip, port, b'get_camera')
    if data:
        np_array = np.frombuffer(data, dtype=np.uint8)
        img = cv2.imdecode(np_array, cv2.IMREAD_COLOR)
        if img is not None:
            return img
        else:
            print("Image could not be decoded")
    return None

class UDPImageApp(App):
    def build(self):
        self.img_widget = Image()
        layout = BoxLayout()
        layout.add_widget(self.img_widget)
        threading.Thread(target=self.image_loop, daemon=True).start()
        return layout

    def image_loop(self):
        last_time = time.time()
        while True:
            image = receive_image(ip, port)
            current_time = time.time()
            dt = current_time - last_time
            last_time = current_time
            fps = 1.0 / dt if dt > 0 else 0
            print(f"UPS: {fps:.2f}")

            if image is not None:
                # Convert BGR to RGB
                image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
                Clock.schedule_once(lambda dt, img=image: self.update_texture(img))

    def update_texture(self, frame):
        # Get image shape
        h, w, _ = frame.shape
        texture = Texture.create(size=(w, h))
        texture.blit_buffer(frame.flatten(), colorfmt='rgb', bufferfmt='ubyte')
        texture.flip_vertical()
        self.img_widget.texture = texture

if __name__ == "__main__":
    UDPImageApp().run()
