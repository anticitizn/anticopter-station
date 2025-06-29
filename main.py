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
# Initialize the last print time
last_motor_callback_time = 0

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
        position_string = re.findall(r'Position\[(.*?)\]', imu_data)[0]
        orientation_string = re.findall(r'Orientation\[(.*?)\]', imu_data)[0]

        acceleration = list(map(float, acceleration_string.split(',')))
        angular_rate = list(map(float, angular_rate_string.split(',')))
        temperature = float(temperature_string)
        position = list(map(float, position_string.split(',')))
        orientation = list(map(float, orientation_string.split(',')))

        return [acceleration, angular_rate, temperature, position, orientation]

def update_texture(image):
    if image is not None:
        image = cv2.cvtColor(image, cv2.COLOR_BGR2RGBA)
        image = image.astype(np.float32) / 255.0

        dpg.set_value("texture_tag", image.ravel())

def update_thread(ip, port):
    last_time = time.time()
    #send_data("set_res_hd\0", "")
    while True:
        image = receive_image(ip, port)
        if image is not None:
            update_texture(image)

        imu_data = None
        #imu_data = receive_imu(ip, port)
        #print(imu_data)

        if imu_data:
            acceleration = imu_data[0]
            angular_rate = imu_data[1]
            temperature = imu_data[2]
            position = imu_data[3]
            orientation = imu_data[4]

            dpg.set_value("imu_textfield", f"Acceleration: {acceleration}\nAngular rate: {angular_rate}\nTemperature:{temperature}")

            for i in range(3):
                acceleration_array[i].append(acceleration[i])
                angular_rate_array[i].append(angular_rate[i])

                # Ensure the arrays are of proper length (e.g., trim to a specific size for visualization).
                if len(acceleration_array[i]) > 100:
                    acceleration_array[i].pop(0)  # Limit the size for performance.
                if len(angular_rate_array[i]) > 100:
                    angular_rate_array[i].pop(0)

            #print(acceleration_array)
            #print(angular_rate_array)

            dpg.set_value("acc_x_series", [dummy_array, acceleration_array[0]])
            dpg.set_value("acc_y_series", [dummy_array, acceleration_array[1]])
            dpg.set_value("acc_z_series", [dummy_array, acceleration_array[2]])

            dpg.set_value("ang_x_series", [dummy_array, angular_rate_array[0]])
            dpg.set_value("ang_y_series", [dummy_array, angular_rate_array[1]])
            dpg.set_value("ang_z_series", [dummy_array, angular_rate_array[2]])

            print(f"Orientation: {orientation}, Position: {position}")

        current_time = time.time()
        dt = current_time - last_time
        last_time = current_time
        fps = 1.0 / dt if dt > 0 else 0
        #print(f"UPS: {fps:.2f}")

        last_time = current_time
        

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

def update_motors(sender, app_data, user_data):
    global last_motor_callback_time

    x, y, pwm_input, _ = app_data
    pwm = int(round(pwm_input))

    # Define position vectors for each motor
    motor_dirs = {
        0: (1, 1),    # top-right
        1: (1, -1),   # bottom-right
        2: (-1, -1),  # bottom-left
        3: (-1, 1),   # top-left
    }

    motor_pwms = []
    for i in range(4):
        mx, my = motor_dirs[i]
        distance = abs(math.sqrt(math.pow(mx - x, 2) + math.pow(my - y, 2)))
        #print(f"Distance M{i}: {distance}")
        motor_pwm = pwm -(max(distance - 1.414, 0) / 1.414)*pwm
        motor_pwms.append(int(round(motor_pwm)))

    mot0, mot1, mot2, mot3 = motor_pwms
    payload = f"{mot0} {mot1} {mot2} {mot3}" + '\0'

    # Rate limit the callback to 20 Hz to prevent overloading the drone with too many separate UDP requests
    current_time = time.time()
    if current_time - last_motor_callback_time >= 0.05:
        print(f"Motors -> M0: {mot0}, M1: {mot1}, M2: {mot2}, M3: {mot3}")
        send_data("set_motors\0", payload)
        last_motor_callback_time = current_time


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

        # IMU Plots
        with dpg.plot(label="IMU Data", height=200, width=600):
            dpg.add_plot_legend()

            dpg.add_plot_axis(dpg.mvXAxis, label="Time")
            dpg.set_axis_limits(dpg.last_item(), 0, 100)
            y_axis = dpg.add_plot_axis(dpg.mvYAxis, label="Values")

            dpg.add_line_series([], [], label="Acc X", parent=y_axis, tag="acc_x_series")
            dpg.add_line_series([], [], label="Acc Y", parent=y_axis, tag="acc_y_series")
            dpg.add_line_series([], [], label="Acc Z", parent=y_axis, tag="acc_z_series")

        with dpg.plot(label="IMU Data", height=200, width=600):
            dpg.add_plot_legend()

            dpg.add_plot_axis(dpg.mvXAxis, label="Time")
            dpg.set_axis_limits(dpg.last_item(), 0, 100)
            y_axis = dpg.add_plot_axis(dpg.mvYAxis, label="Values")

            dpg.add_line_series([], [], label="Ang X", parent=y_axis, tag="ang_x_series")
            dpg.add_line_series([], [], label="Ang Y", parent=y_axis, tag="ang_y_series")
            dpg.add_line_series([], [], label="Ang Z", parent=y_axis, tag="ang_z_series")
                
        # Camera image
        dpg.add_image("texture_tag")
        dpg.add_button(label="EMERGENCY OFF", callback=lambda n: send_data("set_motors\0", f"{0} {0} {0} {0}" + '\0'))

        # LED control
        with dpg.group(horizontal=True):
            dpg.add_color_picker((255, 0, 255, 255), width=150, tag='led_colorpicker')
            with dpg.group(horizontal=False):
                dpg.add_button(label="LED 1", tag="led0", callback=update_leds, user_data='0')
                dpg.add_button(label="LED 2", tag="led1", callback=update_leds, user_data='1')
                dpg.add_button(label="LED 3", tag="led2", callback=update_leds, user_data='2')
                dpg.add_button(label="LED 4", tag="led3", callback=update_leds, user_data='3')
                dpg.add_button(label="All LEDs", tag="led_all", callback=update_leds, user_data='4')

            dpg.add_3d_slider(tag="motors_slider", scale=0.5, min_x=-1, max_x=1, min_y=-1, max_y=1, min_z=0, max_z=100, callback=update_motors)

    dpg.setup_dearpygui()
    dpg.show_viewport()
    dpg.set_primary_window("primary_window", True)

    # Start the image receiving thread
    threading.Thread(target=update_thread, args=(ip, port), daemon=True).start()

    dpg.start_dearpygui()
    dpg.destroy_context()

if __name__ == "__main__":
    main()
