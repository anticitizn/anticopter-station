import socket
import cv2
import numpy as np
import threading
import dearpygui.dearpygui as dpg
import time
import math
import re

ip = "192.168.4.1"
port = 3333

acceleration_array = [[], [], []]
angular_rate_array = [[], [], []]
dummy_array = []

last_motor_callback_time = 0
last_control_callback_time = 0

# Thread-safe shared buffers
image_lock = threading.Lock()
latest_image = None

imu_lock = threading.Lock()
latest_imu = None


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
    sock.settimeout(1.0)

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
    sock.settimeout(0.5)

    try:
        message = command

        sock.sendto(message, (ip, port))

        try:
            # Receive image data
            data, _ = sock.recvfrom(65536)
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
            #print(f"Received image")
        else:
            print(f"Image could not be decoded")

        return image

def receive_imu(ip, port):
    data = get_data(ip, port, b'get_imu')
    if data is not None:
        imu_data = data.decode('utf-8')

        acceleration_string = re.findall(r'Acceleration\[(.*?)\]', imu_data)[0]
        angular_rate_string = re.findall(r'AngularRate\[(.*?)\]', imu_data)[0]
        temperature_string = re.findall(r'Temperature\[(.*?)\]', imu_data)[0]
        #position_string = re.findall(r'Position\[(.*?)\]', imu_data)[0]
        orientation_string = re.findall(r'Orientation\[(.*?)\]', imu_data)[0]

        acceleration = list(map(float, acceleration_string.split(',')))
        angular_rate = list(map(float, angular_rate_string.split(',')))
        temperature = float(temperature_string)
        #position = list(map(float, position_string.split(',')))
        orientation = list(map(float, orientation_string.split(',')))

        return [acceleration, angular_rate, temperature, orientation]

def update_texture(image):
    if image is not None:
        image = cv2.cvtColor(image, cv2.COLOR_BGR2RGBA)
        image = image.astype(np.float32) / 255.0

        dpg.set_value("texture_tag", image.ravel())

def update_thread(ip, port):
    global latest_image, latest_imu

    while True:
        img = receive_image(ip, port)
        if img is not None:
            with image_lock:
                latest_image = img.copy()

        imu = receive_imu(ip, port)
        if imu is not None:
            with imu_lock:
                latest_imu = imu


def gui_update_callback():
    global latest_image, latest_imu

    # Update image
    with image_lock:
        if latest_image is not None:
            img = latest_image.copy()
        else:
            img = None

    if img is not None:
        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGBA)
        img = img.astype(np.float32) / 255.0
        dpg.set_value("texture_tag", img.ravel())

    # Update IMU
    with imu_lock:
        imu = latest_imu

    if imu is not None:
        acceleration, angular_rate, temperature, orientation = imu

        dpg.set_value("imu_textfield",
                      f"Acceleration: {acceleration}\n"
                      f"Angular rate: {angular_rate}\n"
                      f"Temperature:{temperature}")

        for i in range(3):
            acceleration_array[i].append(acceleration[i])
            angular_rate_array[i].append(orientation[i])
            if len(angular_rate_array[i]) > 100:
                angular_rate_array[i].pop(0)

        dpg.set_value("ang_x_series", [dummy_array, angular_rate_array[0]])
        dpg.set_value("ang_y_series", [dummy_array, angular_rate_array[1]])
        dpg.set_value("ang_z_series", [dummy_array, angular_rate_array[2]])
        

def update_leds(sender, app_data, user_data):
    # Convert the color values from float (0.0-1.0) to integer (0-255)
    r, g, b, _ = [int(c) for c in dpg.get_value('led_colorpicker')]

    # Combine the RGB values into a payload string
    payload = user_data + " " + f"{r} {g} {b}" + '\0'

    # Call the send_data function with the prepared payload
    send_data("set_led\0", payload)

def update_control(sender, app_data, user_data):
    global last_control_callback_time

    thrust = dpg.get_value('thrust_slider')
    roll = dpg.get_value('roll_slider')
    pitch = dpg.get_value('pitch_slider')
    yaw = dpg.get_value('yaw_slider')

    payload = f"{thrust} {roll} {pitch} {yaw}" + '\0'
    
    send_data("set_control_target\0", payload)


def main():
    for i in range(100):
        dummy_array.append(i)

    dpg.create_context()
    dpg.create_viewport(title='Custom Title', width=650, height=650)

    with dpg.texture_registry(show=False):
        width, height = 1280, 720
        texture_data = np.zeros((height, width, 4), dtype=np.float32).flatten().tolist()
        dpg.add_dynamic_texture(width=width, height=height, default_value=texture_data, tag="texture_tag")

    with dpg.window(tag="primary_window"):
        dpg.add_text("--- IMU data should go here ---", tag="imu_textfield")

        # LED control
        with dpg.group(horizontal=True):
            with dpg.plot(label="Orientation", height=200, width=600):
                dpg.add_plot_legend()

                dpg.add_plot_axis(dpg.mvXAxis, label="Time")
                dpg.set_axis_limits(dpg.last_item(), 0, 100)
                y_axis = dpg.add_plot_axis(dpg.mvYAxis, label="Values", tag="ang_y_axis")

                dpg.add_line_series([], [], label="Ang X", parent=y_axis, tag="ang_x_series")
                dpg.add_line_series([], [], label="Ang Y", parent=y_axis, tag="ang_y_series")
                dpg.add_line_series([], [], label="Ang Z", parent=y_axis, tag="ang_z_series")
                dpg.set_axis_limits("ang_y_axis", -190, 190)

            with dpg.group():
                dpg.add_button(label="EMERGENCY OFF", callback=lambda n: send_data("set_control_target\0", f"{0} {0} {0} {0}" + '\0'), width=300)
                dpg.add_slider_float(label="Thrust", tag="thrust_slider", max_value=100, min_value = 0, width=300, callback=update_control)
                dpg.add_slider_float(label="Roll", tag="roll_slider", max_value=10, min_value = -10, width=300, callback=update_control)
                dpg.add_slider_float(label="Pitch", tag="pitch_slider", max_value=10, min_value = -10, width=300, callback=update_control)
                dpg.add_slider_float(label="Yaw", tag="yaw_slider", max_value=180, min_value = -180, width=300, callback=update_control)

            dpg.add_color_picker((255, 0, 255, 255), width=150, tag='led_colorpicker')
            with dpg.group(horizontal=False):
                dpg.add_button(label="LED 1", tag="led0", callback=update_leds, user_data='0')
                dpg.add_button(label="LED 2", tag="led1", callback=update_leds, user_data='1')
                dpg.add_button(label="LED 3", tag="led2", callback=update_leds, user_data='2')
                dpg.add_button(label="LED 4", tag="led3", callback=update_leds, user_data='3')
                dpg.add_button(label="All LEDs", tag="led_all", callback=update_leds, user_data='4')
                
        # Camera image
        dpg.add_image("texture_tag")

    dpg.setup_dearpygui()
    dpg.show_viewport()
    dpg.set_primary_window("primary_window", True)

    # Start the image receiving thread
    threading.Thread(target=update_thread, args=(ip, port), daemon=True).start()

    while(dpg.is_dearpygui_running()):
        gui_update_callback()
        dpg.render_dearpygui_frame()   
    dpg.destroy_context()

if __name__ == "__main__":
    main()
