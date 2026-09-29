#!/usr/bin/env python3

import argparse
import json
import math
import os
import socket
import struct
import time
import pygame
import dearpygui.dearpygui as dpg
import cv2
import numpy as np
from collections import deque
from dataclasses import dataclass, field
from enum import IntEnum, IntFlag
from pathlib import Path

MAGIC_NUMBER, PROTOCOL_VERSION = 322, 1
HEADER = struct.Struct("<HBHBIIIH")
DRONE_IP, DRONE_PORT = "192.168.4.1", 3333
RECONNECT_SEC, LINK_TIMEOUT_SEC = 1.0, 2.0
PID_CONFIG_FILE = Path("pid_config.json")
PID_NAMES = [f"{mode}_{axis}" for mode in ("rate", "angle")
             for axis in ("roll", "pitch", "yaw")]
PID_FIELDS = ("kP", "kI", "kD", "int_up_lim", "int_low_lim")
ZERO = (0.0, 0.0, 0.0, 0.0)
TARGET = struct.Struct("<4f")
ZERO_TARGET = TARGET.pack(*ZERO)
MODES = {"pwm": 0, "rate": 1, "angle": 2}
MAPPING = {
    "left_x_axis": 0, "left_y_axis": 1,
    "right_x_axis": 3, "right_y_axis": 4,
    "left_trigger_axis": 2, "right_trigger_axis": 5,
    "b_button": 0, "x_button": 3,
}

FRAGMENT_START = 0  


class MsgType(IntEnum):
    MSG_INIT = 0
    MSG_ACK = 1
    MSG_ERROR = 2
    MSG_ARM = 10
    MSG_DISARM = 11
    MSG_CONTROL_MODE = 12
    MSG_CONTROL_TARGET = 13
    MSG_CFG_PID = 32
    MSG_IMU = 40
    MSG_CAMERA = 50
    MSG_START_RECORDING = 51
    MSG_STOP_RECORDING = 52


class MsgFlag(IntFlag):
    MSG_FLAG_FRAGMENTED = 1
    MSG_FLAG_FRAGMENTED_LAST = 2
    MSG_FLAG_WRITE = 4


@dataclass
class Pending:
    kind: int
    deadline: float
    write: bool = False
    fragments: dict = field(default_factory=dict)
    last: int = None


class AnticopterClient:
    def __init__(self, ip=DRONE_IP, port=DRONE_PORT):
        self.remote = (ip, port)
        self.sock = None
        self.seq = 1
        self.started = time.monotonic()
        self.last_rx = self.next_retry = self.next_imu = self.next_camera = 0.0
        self.connected = False
        self.generation = 0
        self.status = "Disconnected; waiting to connect"
        self.pending = {}
        self.latest_imu = self.latest_jpeg = None

    def close(self):
        if self.sock is not None:
            self.sock.close()
        self.sock = None
        self.connected = False
        self.pending.clear()

    def _disconnect(self, reason):
        self.disarm()
        self.close()
        self.latest_imu = self.latest_jpeg = None
        self.next_retry = time.monotonic() + RECONNECT_SEC
        self.status = f"Disconnected: {reason}; retry in {RECONNECT_SEC:g}s"

    def _send(self, kind, payload=b"", write=False, track=True):
        seq = self.seq
        self.seq = (seq + 1) & 0xFFFFFFFF or 1
        timestamp = int((time.monotonic() - self.started) * 1_000_000) & 0xFFFFFFFF
        flags = int(MsgFlag.MSG_FLAG_WRITE) if write else 0
        packet = HEADER.pack(MAGIC_NUMBER, PROTOCOL_VERSION, kind, flags,
                             seq, 0, timestamp, len(payload)) + payload
        self.sock.sendto(packet, self.remote)
        if track:
            self.pending[seq] = Pending(kind, time.monotonic() + 1.0, write)

    def write(self, kind, payload=b""):
        if not self.connected:
            # Make sure queue stale ARM commands don't get queued up
            return False  
        try:
            self._send(kind, payload, write=True, track=kind != MsgType.MSG_CONTROL_TARGET)
            return True
        except OSError as exc:
            self._disconnect(str(exc))
            return False

    def disarm(self):
        # Keep the UDP retries and try DISARM even if the zero-target send fails
        if self.sock is None:
            return False
        sent = False
        for _ in range(3):
            for kind, payload in ((MsgType.MSG_CONTROL_TARGET, ZERO_TARGET),
                                  (MsgType.MSG_DISARM, b"")):
                try:
                    self._send(kind, payload, write=True, track=False)
                except OSError:
                    continue
                if kind == MsgType.MSG_DISARM:
                    sent = True
        return sent

    def _receive(self, packet, peer):
        if peer != self.remote or len(packet) < HEADER.size:
            return
        magic, version, kind, flags, seq, frag, _, size = HEADER.unpack_from(packet)
        if magic != MAGIC_NUMBER or version not in (0, PROTOCOL_VERSION):
            return
        if len(packet) != HEADER.size + size:
            return
        # Firmware also permits zero response sequences, so only match a pending
        # response of the right type; ACKs must never satisfy camera/IMU reads
        def matches(p):
            if kind == MsgType.MSG_ERROR:
                return True
            if p.write:
                return kind == MsgType.MSG_ACK
            if p.kind == MsgType.MSG_INIT:
                return kind in (MsgType.MSG_INIT, MsgType.MSG_ACK)
            return kind == p.kind

        key = seq if seq else next((k for k, p in self.pending.items() if matches(p)), None)
        pending = self.pending.get(key)
        if pending is None or not matches(pending) or time.monotonic() > pending.deadline:
            return
        payload = packet[HEADER.size:]
        if kind == MsgType.MSG_ERROR:
            self._disconnect("Drone error: " + payload.decode("utf-8", errors="replace").rstrip("\0"))
            return
        if flags & MsgFlag.MSG_FLAG_FRAGMENTED:
            if not FRAGMENT_START <= frag < 4096:
                return
            pending.fragments[frag] = payload
            if sum(map(len, pending.fragments.values())) > 4 * 1024 * 1024:
                del self.pending[key]
                return
            if flags & MsgFlag.MSG_FLAG_FRAGMENTED_LAST:
                pending.last = frag
            if pending.last is None or not all(i in pending.fragments for i in range(FRAGMENT_START, pending.last + 1)):
                return
            payload = b"".join(pending.fragments[i] for i in range(FRAGMENT_START, pending.last + 1))
        if kind == MsgType.MSG_IMU and len(payload) < 64:
            return
        del self.pending[key]
        self.last_rx = time.monotonic()

        if not self.connected:
            # Even if INIT ACK is not received, IMU packet can establish connection
            self.connected = True
            self.generation += 1
            self.status = f"Connected to {self.remote[0]}:{self.remote[1]}"
        if kind == MsgType.MSG_IMU:
            self.latest_imu = struct.unpack("<16f", payload[:64])
        elif kind == MsgType.MSG_CAMERA:
            self.latest_jpeg = payload

    def tick(self):
        now = time.monotonic()
        try:
            if self.sock is None:
                if now < self.next_retry:
                    return
                self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                self.sock.setblocking(False)
                self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024 * 1024)
                self.last_rx = now
                self.status = f"Connecting to {self.remote[0]}:{self.remote[1]}..."
                self._send(MsgType.MSG_INIT, struct.pack("<I", int((now - self.started) * 1_000_000) & 0xFFFFFFFF))
            # Bound work per frame, even during a camera burst.
            for _ in range(512):
                try:
                    packet, peer = self.sock.recvfrom(65536)
                except BlockingIOError:
                    break
                self._receive(packet, peer)
                if self.sock is None:
                    return
            if now - self.last_rx >= LINK_TIMEOUT_SEC:
                self._disconnect("no response")
                return
            self.pending = {k: p for k, p in self.pending.items() if p.deadline > now}
            for kind, attr, interval in ((MsgType.MSG_IMU, "next_imu", 0.05),
                                         (MsgType.MSG_CAMERA, "next_camera", 0.01)):
                if kind == MsgType.MSG_CAMERA and not self.connected:
                    continue
                if now >= getattr(self, attr) and not any(
                        p.kind == kind and not p.write for p in self.pending.values()):
                    self._send(kind)
                    setattr(self, attr, now + interval)
        except OSError as exc:
            self._disconnect(str(exc))


def shape_axis(value, deadzone, expo):
    value = max(-1.0, min(1.0, value))
    scaled = max(0.0, (abs(value) - deadzone) / (1.0 - deadzone))
    return math.copysign((1.0 - expo) * scaled + expo * scaled ** 3, value)


def gamepad_target(axes, args):
    raw = axes[args.left_y_axis]
    throttle = 0.0 if raw >= 0.92 else (1.0 - max(-1.0, min(1.0, raw))) * 0.5 * args.max_throttle
    roll = shape_axis(axes[args.right_x_axis], args.deadzone, args.expo) * args.max_roll
    pitch = -shape_axis(axes[args.right_y_axis], args.deadzone, args.expo) * args.max_pitch
    yaw = shape_axis(axes[args.left_x_axis], args.deadzone, args.expo) * args.max_yaw
    return throttle, roll, pitch, yaw


class Gamepad:
    def __init__(self, pygame, args):
        self.pg, self.args = pygame, args
        self.joy = None
        self.next_scan = 0.0
        self.status = "No controller; waiting"
        self.axes, self.buttons, self.hats = [], [], []
        self.ready = self.lost = False
        self.pg.display.init()
        self.pg.joystick.init()

    def disarm_pressed(self):
        return self.ready and (self.buttons[self.args.b_button] or any(
            self.axes[getattr(self.args, name)] >= self.args.trigger_threshold
            for name in ("left_trigger_axis", "right_trigger_axis")))

    def poll(self):
        self.lost = False
        try:
            for event in self.pg.event.get():
                if (event.type == self.pg.JOYDEVICEREMOVED and self.joy is not None
                        and event.instance_id == self.joy.get_instance_id()):
                    self.joy.quit()
                    self.joy = None
                    self.lost = True
            if self.joy is None and time.monotonic() >= self.next_scan:
                self.next_scan = time.monotonic() + 1.0
                if self.args.controller < self.pg.joystick.get_count():
                    self.joy = self.pg.joystick.Joystick(self.args.controller)
                    self.joy.init()
                    self.pg.event.pump()
            self.ready = False
            if self.joy is None:
                self.axes, self.buttons, self.hats = [], [], []
                self.status = "No controller; waiting"
                return
            self.axes = [self.joy.get_axis(i) for i in range(self.joy.get_numaxes())]
            self.buttons = [bool(self.joy.get_button(i)) for i in range(self.joy.get_numbuttons())]
            self.hats = [self.joy.get_hat(i) for i in range(self.joy.get_numhats())]
            self.ready = all(
                0 <= getattr(self.args, name) < (
                    len(self.axes) if name.endswith("axis") else len(self.buttons))
                for name in MAPPING)
            self.status = self.joy.get_name() + ("" if self.ready else " - invalid mapping; use --diagnose")
        except self.pg.error as exc:
            self.joy = None
            self.lost, self.ready = True, False
            self.status = f"Controller unavailable: {exc}; retrying"


def load_pid_configs():
    configs = {name: dict.fromkeys(PID_FIELDS, 0.0) for name in PID_NAMES}
    try:
        data = json.loads(PID_CONFIG_FILE.read_text())
        for name in PID_NAMES:
            for key in PID_FIELDS:
                value = float(data.get(name, {}).get(key, 0.0))
                if math.isfinite(value):
                    configs[name][key] = value
    except FileNotFoundError:
        pass
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        print(f"PID load failed: {exc}")
    return configs


class GroundStation:
    def __init__(self, args, pad):
        self.args, self.pad = args, pad
        self.client = AnticopterClient(args.ip, args.port)
        self.armed = False
        self.recording = None
        self.mode = args.mode
        self.target = ZERO
        self.previous_x = True  # Requires release then press after reconnect
        self.previous_disarm = False
        self.generation = 0
        self.next_control = 0.0
        self.history = [deque(maxlen=100) for _ in range(3)]

    def disarm(self, note=None):
        self.armed = False
        self.target = ZERO
        sent = self.client.disarm()
        dpg.set_value("command_status", note or (
            "DISARM sent" if sent else "Controls disabled; DISARM not sent"))

    def arm(self):
        if self.pad.ready:
            self.target = gamepad_target(self.pad.axes, self.args)
        if not self.client.connected:
            note = "ARM rejected: drone disconnected"
        elif not self.pad.ready:
            note = "ARM rejected: controller unavailable or invalid mapping"
        elif self.pad.disarm_pressed():
            note = "ARM rejected: release B and both triggers"
        elif self.target[0] > self.args.arm_throttle_max:
            note = "ARM rejected: lower throttle first"
        else:
            self.client.write(MsgType.MSG_CONTROL_TARGET, ZERO_TARGET)
            self.armed = self.client.write(MsgType.MSG_ARM)
            note = "ARM sent" if self.armed else "ARM failed: disconnected"
        dpg.set_value("command_status", note)

    def set_mode(self, _sender, _app_data, mode):
        self.mode = mode
        sent = self.client.write(MsgType.MSG_CONTROL_MODE, struct.pack("<I", MODES[mode]))
        note = "sent" if sent else "selected; waiting for connection"
        dpg.set_value("command_status", f"{mode.upper()} HOLD {note}")

    def toggle_recording(self):
        recording = not self.recording
        kind = MsgType.MSG_START_RECORDING if recording else MsgType.MSG_STOP_RECORDING
        if self.client.write(kind):
            self.recording = recording
        else:
            dpg.set_value("command_status", "Recording command not sent: disconnected")

    def apply_pid(self):
        try:
            configs = {name: {key: float(dpg.get_value(f"{name}_{key}")) for key in PID_FIELDS} for name in PID_NAMES}
            values = [configs[name][key] for name in PID_NAMES for key in PID_FIELDS]
            if not all(math.isfinite(v) for v in values):
                raise ValueError("PID values must be finite")
            payload = struct.pack("<30f", *values)
            PID_CONFIG_FILE.write_text(json.dumps(configs, indent=2))
            sent = self.client.write(MsgType.MSG_CFG_PID, payload)
            note = "PID saved; " + ("command sent" if sent else "offline; press APPLY again when connected")
        except (OSError, ValueError, OverflowError, struct.error) as exc:
            note = f"PID failed: {exc}"
        dpg.set_value("command_status", note)

    def update_inputs(self):
        self.pad.poll()
        if self.pad.lost or not self.pad.ready:
            if self.armed or self.pad.lost:
                self.disarm("Controller lost; arm again after reconnect")
            self.target, self.previous_x = ZERO, True
            self.previous_disarm = False
            return
        self.target = gamepad_target(self.pad.axes, self.args)
        x = self.pad.buttons[self.args.x_button]
        disarm_pressed = self.pad.disarm_pressed()
        if disarm_pressed:
            if self.armed or not self.previous_disarm:
                self.disarm()
        elif x and not self.previous_x:
            self.arm()
        self.previous_x = x
        self.previous_disarm = disarm_pressed

    def tick(self):
        self.client.tick()
        if self.client.generation != self.generation:
            self.generation = self.client.generation
            self.disarm("Connected; arm to enable controls")
            self.previous_x = True
            self.recording = None
            self.client.write(MsgType.MSG_CONTROL_MODE, struct.pack("<I", MODES[self.mode]))
        if not self.client.connected:
            if self.armed:
                self.disarm("Connection lost; arm again after reconnect")
            self.recording = None
            self.previous_x = True
        self.update_inputs()
        # Callbacks and pygame run on this thread, so input cannot race DISARM.
        dpg.run_callbacks(dpg.get_callback_queue())
        now = time.monotonic()
        if self.client.connected and now >= self.next_control:
            target = self.target if self.armed else ZERO
            self.client.write(MsgType.MSG_CONTROL_TARGET, TARGET.pack(*target))
            self.next_control = now + 1.0 / self.args.rate
        dpg.set_value("connection_status", self.client.status)
        dpg.set_value("gamepad_status", self.pad.status)
        dpg.set_value("control_status", (
            f"{'ARM sent' if self.armed else 'Controls disabled'} | "
            f"{self.mode.upper()} HOLD | T/R/P/Y: "
            + ", ".join(f"{v:.1f}" for v in self.target)))
        dpg.configure_item("recording_button", label="STOP RECORDING" if self.recording else "START RECORDING")
        self.update_telemetry()

    def update_telemetry(self):
        imu, jpeg = self.client.latest_imu, self.client.latest_jpeg
        self.client.latest_imu = self.client.latest_jpeg = None
        if imu is not None:
            for i, series in enumerate(self.history):
                series.append(imu[6 + i])
                dpg.set_value(f"ang_{i}_series", [list(range(len(series))), list(series)])
        if jpeg:
            try:
                image = cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
                if image is not None:
                    image = cv2.resize(image, (640, 480))
                    rgba = cv2.cvtColor(image, cv2.COLOR_BGR2RGBA).astype(np.float32) / 255.0
                    dpg.set_value("texture_tag", rgba.ravel())
            except cv2.error:
                # A lost or corrupt camera frame must not stop controls
                pass  

    def build_ui(self):
        with dpg.texture_registry(show=False):
            dpg.add_dynamic_texture(640, 480, np.zeros(640 * 480 * 4, dtype=np.float32), tag="texture_tag")
        with dpg.window(tag="primary_window"):
            with dpg.group(horizontal=True):
                dpg.add_image("texture_tag")
                with dpg.group():
                    for tag, value in (("connection_status", self.client.status), ("gamepad_status", self.pad.status),
                                    ("control_status", "Controls disabled"), ("command_status", "Ready"),):
                        dpg.add_text(value, tag=tag)
                    with dpg.group(horizontal=True):
                        with dpg.plot(label="Orientation", height=220, width=620):
                            dpg.add_plot_legend()
                            x_axis = dpg.add_plot_axis(dpg.mvXAxis, label="Samples")
                            dpg.set_axis_limits(x_axis, 0, 100)
                            y_axis = dpg.add_plot_axis(dpg.mvYAxis, label="Degrees")
                            dpg.set_axis_limits(y_axis, -190, 190)
                            for i, label in enumerate(("Roll", "Pitch", "Yaw")):
                                dpg.add_line_series([], [], label=label, parent=y_axis, tag=f"ang_{i}_series")
                        with dpg.group():
                            for label, callback in (("ARM", self.arm), ("DISARM", self.disarm)):
                                dpg.add_button(label=label, callback=lambda _s, _a, fn: fn(),
                                               user_data=callback, width=300, height=40)
                            dpg.add_button(label="START RECORDING", tag="recording_button",
                                           callback=self.toggle_recording, width=300, height=40)
                            for mode in MODES:
                                dpg.add_button(label=f"{mode.upper()} HOLD", callback=self.set_mode,
                                               user_data=mode, width=300)

            dpg.add_separator()
            dpg.add_text(f"PID configuration loaded from {PID_CONFIG_FILE}")
            configs = load_pid_configs()
            with dpg.table(header_row=True, resizable=True, policy=dpg.mvTable_SizingFixedFit,
                           borders_innerH=True, borders_outerH=True, borders_innerV=True, borders_outerV=True):
                for label in ("Controller", "kP", "kI", "kD", "Int Up Lim", "Int Low Lim"):
                    dpg.add_table_column(label=label)
                for name in PID_NAMES:
                    with dpg.table_row():
                        dpg.add_text(name.replace("_", " ").title())
                        for key in PID_FIELDS:
                            dpg.add_input_float(tag=f"{name}_{key}", default_value=configs[name][key],
                                                format="%.2f", width=110, step=0)
            dpg.add_button(label="APPLY PID CONFIG", callback=self.apply_pid, width=260, height=40)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ip", default=DRONE_IP, help="Drone IPv4 address")
    parser.add_argument("--port", type=int, default=DRONE_PORT)
    parser.add_argument("--controller", type=int, default=0)
    parser.add_argument("--mode", choices=MODES, default="rate")
    parser.add_argument("--diagnose", action="store_true",
                        help="Print controller inputs without connecting to the drone")
    defaults = dict(rate=50.0, deadzone=0.08, expo=0.25, max_throttle=100.0,
                    max_roll=10.0, max_pitch=10.0, max_yaw=10.0,
                    arm_throttle_max=5.0, trigger_threshold=0.55)
    for name, value in {**defaults, **MAPPING}.items():
        parser.add_argument("--" + name.replace("_", "-"), type=type(value), default=value)
    args = parser.parse_args(argv)
    try:
        socket.inet_pton(socket.AF_INET, args.ip)
    except OSError:
        parser.error("--ip must be an IPv4 address")
    if not 1 <= args.port <= 65535 or args.controller < 0:
        parser.error("invalid port or controller index")
    if not all(math.isfinite(getattr(args, k)) for k in defaults):
        parser.error("control settings must be finite")
    if not (1 <= args.rate <= 250 and 0 <= args.deadzone < 0.95 and 0 <= args.expo <= 1):
        parser.error("rate: 1..250, deadzone: [0, 0.95), expo: 0..1")
    if any(getattr(args, k) < 0 for k in MAPPING) or any(
            not 0 < getattr(args, k) <= 10000 for k in defaults if k.startswith("max_")):
        parser.error("mapping indices must be nonnegative and maximum controls must be in (0, 10000]")
    if not 0 <= args.arm_throttle_max <= args.max_throttle or not -1 <= args.trigger_threshold <= 1:
        parser.error("invalid arm throttle limit or trigger threshold")
    return args


def main():
    args = parse_args()
    os.environ.setdefault("SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS", "1")
    os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

    pad = Gamepad(pygame, args)
    if args.diagnose:
        try:
            while True:
                pad.poll()
                pressed = [i for i, value in enumerate(pad.buttons) if value]
                print(f"\r{pad.status} | axes={[round(v, 3) for v in pad.axes]} "
                      f"buttons={pressed} hats={pad.hats}    ", end="", flush=True)
                time.sleep(0.08)
        except KeyboardInterrupt:
            print()
        finally:
            pygame.quit()
        return
    
    app = GroundStation(args, pad)
    dpg.create_context()
    try:
        dpg.configure_app(manual_callback_management=True)
        dpg.create_viewport(title="Anticopter Ground Station", width=1600, height=800, vsync=False)
        app.build_ui()
        dpg.setup_dearpygui()
        dpg.show_viewport()
        dpg.set_primary_window("primary_window", True)
        while dpg.is_dearpygui_running():
            app.tick()
            dpg.render_dearpygui_frame()
            time.sleep(0.001)
    except KeyboardInterrupt:
        pass
    finally:
        app.client.disarm()
        app.client.close()
        pygame.quit()
        dpg.destroy_context()


if __name__ == "__main__":
    main()

