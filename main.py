import socket
import cv2
import numpy as np
import threading
import dearpygui.dearpygui as dpg
import time

ip = "192.168.178.27"
port = 3333

def create_video_from_images(images, output_filename, fps=12):
    if not images:
        print("No images to create a video.")
        return

    # Get the shape of the images
    height, width, layers = images[0].shape
    size = (width, height)

    # Initialize video writer
    out = cv2.VideoWriter(output_filename, cv2.VideoWriter_fourcc(*'XVID'), fps, size)

    for img in images:
        out.write(img)

    out.release()
    print(f"Video saved as {output_filename}")

def send_data(command, payload):
    # Create a UDP socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(1.0)  # Set timeout to 1 second

    try:
        message = command

        # Ensure the command is exactly 128 bytes long
        if len(message) < 128:
            # Pad the command with null bytes if it's less than 128 bytes
            message = message.ljust(128)
        elif len(message) > 128:
            # Trim the command to 128 bytes if it's longer
            message = message[:128]
        
        # Append the payload after 128 bytes
        message += payload

        print(message)

        message = message.encode('utf-8')
        sock.sendto(message, (ip, port))
        sock.close()
    finally:
        sock.close()
    
def get_data(ip, port, command):
    # Create a UDP socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(1.0)  # Set timeout to 1 second

    try:
        message = command

        sock.sendto(message, (ip, port))

        try:
            # Receive image data
            data, _ = sock.recvfrom(65536)  # Buffer size is 65536 bytes
            if data:
                sock.close()
                return data
        except socket.timeout:
            print("Socket timeout, no response received")
            sock.close()
    finally:
        sock.close()

def receive_image(ip, port):
    data = get_data(ip, port, b'get_camera')
    if data is not None:
        # Convert the byte data to numpy array
        np_array = np.frombuffer(data, dtype=np.uint8)
        # Decode the array into an image
        img = cv2.imdecode(np_array, cv2.IMREAD_COLOR)
        if img is not None:
            image = img
            print(f"Received image")
        else:
            print(f"Image could not be decoded")

        return image

def receive_imu(ip, port):
    data = get_data(ip, port, b'get_imu')
    if data is not None:
        imu_data = data.decode('utf-8')

        return imu_data

def update_texture(image):
    if image is not None:
        height, width, _ = image.shape
        image = cv2.cvtColor(image, cv2.COLOR_BGR2RGBA)
        image = np.flipud(image)  # Flip the image vertically for correct display
        image = np.asfarray(image, dtype='f') / 255.0  # Normalize the image
        dpg.set_value("texture_tag", image.flatten().tolist())

def update_thread(ip, port):
    last_time = time.time()
    while True:
        image = receive_image(ip, port)
        if image is not None:
            current_time = time.time()
            time_diff = current_time - last_time
            fps = 1.0 / time_diff if time_diff > 0 else 0
            print(f"FPS: {fps:.2f}")
            last_time = current_time
            update_texture(image)

        imu_data = receive_imu(ip, port)
        if imu_data is not None:
            dpg.set_value("imu_textfield", imu_data)
        
        time.sleep(0.001)

def update_leds(sender, app_data, user_data):
    # Convert the color values from float (0.0-1.0) to integer (0-255)
    r, g, b, _ = [int(c) for c in dpg.get_value('led_colorpicker')]

    # Combine the RGB values into a payload string
    payload = user_data + " " + f"{r} {g} {b}" + '\0'

    # Example debug prints
    print(f"Sender: {sender}")
    print(f"RGB Values: ({r}, {g}, {b})")
    print(f"Payload: {payload}")

    # Call the send_data function with the prepared payload
    send_data("set_led\0", payload)

def main():
    dpg.create_context()
    dpg.create_viewport(title='Custom Title', width=650, height=650)

    with dpg.texture_registry(show=False):
        width, height = 640, 480
        texture_data = np.zeros((height, width, 4), dtype=np.float32).flatten().tolist()
        dpg.add_dynamic_texture(width=width, height=height, default_value=texture_data, tag="texture_tag")

    with dpg.window(tag="primary_window"):
        dpg.add_text("--- IMU data should go here ---", tag="imu_textfield")
        dpg.add_image("texture_tag")
        with dpg.group(horizontal=True):
            dpg.add_color_picker((255, 0, 255, 255), width=150, tag='led_colorpicker')
            with dpg.group(horizontal=False):
                dpg.add_button(label="LED 1", tag="led0", callback=update_leds, user_data='0')
                dpg.add_button(label="LED 2", tag="led1", callback=update_leds, user_data='1')
                dpg.add_button(label="LED 3", tag="led2", callback=update_leds, user_data='2')
                dpg.add_button(label="LED 4", tag="led3", callback=update_leds, user_data='3')
                dpg.add_button(label="All LEDs", tag="led_all", callback=update_leds, user_data='4')

    dpg.setup_dearpygui()
    dpg.show_viewport()
    dpg.set_primary_window("primary_window", True)

    # Start the image receiving thread
    threading.Thread(target=update_thread, args=(ip, port), daemon=True).start()

    dpg.start_dearpygui()
    dpg.destroy_context()

if __name__ == "__main__":
    main()
