import socket
import time
import cv2
import numpy as np

def receive_images(ip, port, n_images):
    # Create a UDP socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(1.0)  # Set timeout to 1 second

    # List to store the received images
    images = []

    try:
        for _ in range(n_images):
            # Send "hello world" message
            message = b'hello world'
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
                        images.append(img)
                        print(f"Received image")
            except socket.timeout:
                print("Socket timeout, no response received")

            time.sleep(0.08)  # Wait for 1 second before sending the next message
    finally:
        sock.close()

    return images

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

def main():
    ip = "192.168.178.27"
    port = 3333
    n_images = int(input("Enter the number of images to receive: "))

    # Receive images
    images = receive_images(ip, port, n_images)

    # Create video from received images
    create_video_from_images(images, 'output_video.avi')

if __name__ == "__main__":
    main()
