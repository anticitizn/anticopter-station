import socket
import struct
import time
from dataclasses import dataclass
from enum import IntEnum, IntFlag
from typing import Dict, Optional, Tuple

import cv2
import numpy as np

IP = "192.168.4.1"
PORT = 3333

MAGIC_NUMBER = 322
PROTOCOL_VERSION = 1

HEADER_FMT = "<HBHBIIIH"
HEADER_SIZE = struct.calcsize(HEADER_FMT)

SOCKET_TIMEOUT_SEC = 0.5
CAMERA_TIMEOUT_SEC = 1.0


class MsgType(IntEnum):
    MSG_INIT = 0
    MSG_ACK = 1
    MSG_ERROR = 2

    MSG_CAMERA = 50


class MsgFlag(IntFlag):
    MSG_FLAG_FRAGMENTED = 1 << 0
    MSG_FLAG_FRAGMENTED_LAST = 1 << 1
    MSG_FLAG_WRITE = 1 << 2


@dataclass
class MessageHeader:
    magic_number: int
    version: int
    msg_type: int
    flags: int
    seq: int
    frag_seq: int
    timestamp: int
    payload_len: int

    def pack(self) -> bytes:
        return struct.pack(
            HEADER_FMT,
            self.magic_number,
            self.version,
            self.msg_type,
            self.flags,
            self.seq,
            self.frag_seq,
            self.timestamp,
            self.payload_len,
        )

    @staticmethod
    def unpack(data: bytes) -> "MessageHeader":
        return MessageHeader(*struct.unpack(HEADER_FMT, data))


class AnticopterCameraClient:
    def __init__(self, ip: str, port: int) -> None:
        self.remote = (ip, port)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.settimeout(SOCKET_TIMEOUT_SEC)
        self.seq = 1
        self.start_time = time.monotonic()
        self._init_session()

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass

    def _next_seq(self) -> int:
        seq = self.seq
        self.seq += 1
        return seq

    def _timestamp_us(self) -> int:
        return int((time.monotonic() - self.start_time) * 1_000_000) & 0xFFFFFFFF

    def _drain_socket(self) -> None:
        self.sock.settimeout(0.0)
        try:
            while True:
                self.sock.recvfrom(65536)
        except (BlockingIOError, socket.timeout, OSError):
            pass
        finally:
            self.sock.settimeout(SOCKET_TIMEOUT_SEC)

    def _send_packet(self, msg_type: MsgType, payload: bytes = b"", flags: MsgFlag = MsgFlag(0), seq: Optional[int] = None) -> int:
        if seq is None:
            seq = self._next_seq()

        header = MessageHeader(
            magic_number=MAGIC_NUMBER,
            version=PROTOCOL_VERSION,
            msg_type=int(msg_type),
            flags=int(flags),
            seq=seq,
            frag_seq=0,
            timestamp=self._timestamp_us(),
            payload_len=len(payload),
        )
        self.sock.sendto(header.pack() + payload, self.remote)
        return seq

    def _recv_message_for_seq(self, expected_seq: int, expected_types: Tuple[int, ...], timeout_sec: float) -> bytes:
        deadline = time.monotonic() + timeout_sec
        fragments: Dict[int, bytes] = {}

        while time.monotonic() < deadline:
            remaining = max(0.001, deadline - time.monotonic())
            self.sock.settimeout(remaining)

            try:
                packet, _ = self.sock.recvfrom(65536)
            except socket.timeout:
                continue

            if len(packet) < HEADER_SIZE:
                continue

            header = MessageHeader.unpack(packet[:HEADER_SIZE])
            payload = packet[HEADER_SIZE:HEADER_SIZE + header.payload_len]

            if header.magic_number != MAGIC_NUMBER:
                continue

            if header.msg_type == MsgType.MSG_ERROR:
                try:
                    text = payload.decode("utf-8", errors="replace").rstrip("\0")
                except Exception:
                    text = repr(payload)
                raise RuntimeError(f"Drone returned MSG_ERROR: {text}")

            if header.msg_type not in expected_types and header.msg_type != MsgType.MSG_ACK:
                continue

            # Current firmware reply headers may only reliably populate
            # magic_number, msg_type, and payload_len.
            seq_matches = (header.seq == expected_seq) or (header.seq == 0)
            version_matches = (header.version == PROTOCOL_VERSION) or (header.version == 0)

            if not seq_matches or not version_matches:
                continue

            is_fragmented = bool(header.flags & MsgFlag.MSG_FLAG_FRAGMENTED)
            if is_fragmented:
                fragments[header.frag_seq] = payload
                if header.flags & MsgFlag.MSG_FLAG_FRAGMENTED_LAST:
                    return b"".join(fragments[idx] for idx in sorted(fragments))
                continue

            return payload

        raise TimeoutError(f"Timed out waiting for response to seq={expected_seq}")

    def _init_session(self) -> None:
        self._drain_socket()
        seq = self._send_packet(MsgType.MSG_INIT, struct.pack("<I", self._timestamp_us()))
        try:
            self._recv_message_for_seq(seq, (MsgType.MSG_ACK, MsgType.MSG_INIT), 0.2)
        except TimeoutError:
            pass

    def get_camera_jpeg(self) -> bytes:
        self._drain_socket()
        seq = self._send_packet(MsgType.MSG_CAMERA)
        return self._recv_message_for_seq(seq, (MsgType.MSG_CAMERA,), CAMERA_TIMEOUT_SEC)


def receive_image(client: AnticopterCameraClient):
    data = client.get_camera_jpeg()
    np_array = np.frombuffer(data, dtype=np.uint8)
    img = cv2.imdecode(np_array, cv2.IMREAD_COLOR)
    if img is None:
        print("Image could not be decoded")
    return img


def display_images(ip: str, port: int):
    client = AnticopterCameraClient(ip, port)
    last_time = time.time()

    try:
        while True:
            try:
                image = receive_image(client)
            except TimeoutError:
                print("Socket timeout, no response received")
                continue
            except RuntimeError as exc:
                print(exc)
                continue

            current_time = time.time()
            dt = current_time - last_time
            last_time = current_time
            fps = 1.0 / dt if dt > 0 else 0.0

            if image is not None:
                cv2.imshow("Camera Feed", image)
                cv2.setWindowTitle("Camera Feed", f"Camera Feed - {fps:.2f} FPS")

            if cv2.waitKey(1) & 0xFF == ord("q"):
                break
    finally:
        client.close()
        cv2.destroyAllWindows()


def main():
    display_images(IP, PORT)


if __name__ == "__main__":
    main()
