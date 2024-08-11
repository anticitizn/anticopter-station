import socket
import cv2
import numpy as np
import threading
import dearpygui.dearpygui as dpg
import time

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

def receive_image(ip, port):
    # Create a UDP socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(1.0)  # Set timeout to 1 second

    # List to store the received images
    image = None

    try:
        # Send "hello world" message
        message = b'get_camera'
        sock.sendto(message, (ip, port))

        try:
            # Receive image data
            data, _ = sock.recvfrom(65536)  # Buffer size is 65536 bytes
            if data:
                # Convert the byte data to numpy array
                np_array = np.frombuffer(data, dtype=np.uint8)
                # Decode the array into an image
                img = cv2.imdecode(np_array, cv2.IMREAD_COLOR)
                if img is not None:
                    image = img
                    print(f"Received image")
                else:
                    print(f"Idk")
        except socket.timeout:
            print("Socket timeout, no response received")
    finally:
        sock.close()

    return image

def update_texture(image):
    if image is not None:
        height, width, _ = image.shape
        image = cv2.cvtColor(image, cv2.COLOR_BGR2RGBA)
        image = np.flipud(image)  # Flip the image vertically for correct display
        image = np.asfarray(image, dtype='f') / 255.0  # Normalize the image
        dpg.set_value("texture_tag", image.flatten().tolist())

def image_thread(ip, port):
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
        time.sleep(0.001)

def main():
    ip = "192.168.178.27"
    port = 3333

    dpg.create_context()
    dpg.create_viewport(title='Custom Title', width=800, height=600)

    with dpg.texture_registry(show=True):
        width, height = 640, 480
        texture_data = np.zeros((height, width, 4), dtype=np.float32).flatten().tolist()
        dpg.add_dynamic_texture(width=width, height=height, default_value=texture_data, tag="texture_tag")

    with dpg.window(label="Example Window"):
        dpg.add_text("Hello, world")
        dpg.add_button(label="Save")
        dpg.add_input_text(label="string", default_value="Quick brown fox")
        dpg.add_slider_float(label="float", default_value=0.273, max_value=1)
        dpg.add_image("texture_tag")    

    dpg.setup_dearpygui()
    dpg.show_viewport()

    # Start the image receiving thread
    threading.Thread(target=image_thread, args=(ip, port), daemon=True).start()

    dpg.start_dearpygui()
    dpg.destroy_context()

if __name__ == "__main__":
    main()
