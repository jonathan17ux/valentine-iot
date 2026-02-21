#!/usr/bin/env python3
"""
Valentine's IoT Client App — v2 (PNG emoji)
Runs on Raspberry Pi Zero 2 W (Chile or Miami)
Uses PNG images for emoji rendering on TFT framebuffer.

Usage:
    python3 client.py --device chile
    python3 client.py --device miami
"""

import os
import sys
import time
import struct
import argparse
import threading
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

import socketio
import requests
from PIL import Image, ImageDraw, ImageFont

# ─── Logging Setup ────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("/tmp/valentine_client.log"),
    ],
)
log = logging.getLogger(__name__)

# ─── Configuration ────────────────────────────────────────────────────────────
SERVER_URL  = "http://10.8.0.1:5000"
FRAMEBUFFER = "/dev/fb1"
SCREEN_W, SCREEN_H = 480, 320
EMOJI_DIR   = Path(__file__).parent / "emoji_cache"

# Touch ADC range
TOUCH_X_MIN, TOUCH_X_MAX = 200, 3900
TOUCH_Y_MIN, TOUCH_Y_MAX = 200, 3900

# Colors
C_BG     = (10,  10,  10)
C_ACCENT = (220, 50,  100)
C_TEXT   = (255, 255, 255)
C_DIM    = (120, 120, 120)
C_GREEN  = (50,  205, 50)
C_RED    = (220, 50,  50)
C_PANEL  = (30,  20,  30)

# Emoji list — (display_char, openmoji_unicode_hex)
EMOJIS = [
    ("heart",   "2764"),
    ("two_hearts", "1F495"),
    ("kiss",    "1F618"),
    ("love",    "1F970"),
    ("rose",    "1F339"),
    ("party",   "1F389"),
    ("eyes",    "1F60D"),
    ("lips",    "1F48B"),
]

# Human-readable labels match to actual emoji chars for sending
EMOJI_CHARS = ["❤️", "💕", "😘", "🥰", "🌹", "🎉", "😍", "💋"]

TZ_CHILE = timezone(timedelta(hours=-3))
TZ_MIAMI = timezone(timedelta(hours=-5))

HEADER_H = 45
PICKER_H = 70

# ─── Emoji PNG Loader ─────────────────────────────────────────────────────────

def download_emoji_pngs():
    """Download emoji PNGs from OpenMoji on first run, cache locally."""
    EMOJI_DIR.mkdir(exist_ok=True)
    base_url = "https://raw.githubusercontent.com/hfg-gmuend/openmoji/master/color/72x72"

    for name, hex_code in EMOJIS:
        dest = EMOJI_DIR / f"{hex_code}.png"
        if dest.exists():
            log.info(f"  Cached: {name}")
            continue
        url = f"{base_url}/{hex_code.upper()}.png"
        log.info(f"  Downloading: {name} ({hex_code})")
        try:
            r = requests.get(url, timeout=15)
            r.raise_for_status()
            dest.write_bytes(r.content)
            log.info(f"  Saved: {dest.name}")
        except Exception as e:
            log.warning(f"  Failed {hex_code}: {e}")


def load_emoji_images(size: int) -> list:
    """Load emoji PNGs resized to `size`, return list matching EMOJIS order."""
    images = []
    for name, hex_code in EMOJIS:
        path = EMOJI_DIR / f"{hex_code}.png"
        if path.exists():
            try:
                img = Image.open(path).convert("RGBA").resize((size, size), Image.LANCZOS)
                images.append(img)
            except Exception as e:
                log.warning(f"Could not load {hex_code}: {e}")
                images.append(None)
        else:
            images.append(None)
    return images


def paste_emoji(canvas: Image.Image, emoji_img, x: int, y: int):
    """Paste RGBA emoji PNG centred at (x, y)."""
    if emoji_img is None:
        return
    cx = x - emoji_img.width  // 2
    cy = y - emoji_img.height // 2
    canvas.paste(emoji_img, (cx, cy), emoji_img)


# ─── Framebuffer Writer ───────────────────────────────────────────────────────

def write_to_framebuffer(img: Image.Image):
    try:
        r, g, b = img.split()
        rb = r.tobytes()
        gb = g.tobytes()
        bb = b.tobytes()
        out = bytearray(SCREEN_W * SCREEN_H * 2)
        for i in range(SCREEN_W * SCREEN_H):
            rv = rb[i] >> 3
            gv = gb[i] >> 2
            bv = bb[i] >> 3
            pixel = (rv << 11) | (gv << 5) | bv
            out[i * 2]     = pixel & 0xFF
            out[i * 2 + 1] = (pixel >> 8) & 0xFF
        with open(FRAMEBUFFER, "wb") as fb:
            fb.write(bytes(out))
    except Exception as e:
        log.warning(f"Framebuffer write error: {e}")


# ─── Font Loader ──────────────────────────────────────────────────────────────

def load_font(size: int, bold: bool = False):
    paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold
            else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf" if bold
            else "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
    ]
    for p in paths:
        if Path(p).exists():
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                continue
    return ImageFont.load_default()


# ─── Touch Reader ─────────────────────────────────────────────────────────────

class TouchReader(threading.Thread):
    EV_SYN, EV_ABS, EV_KEY = 0, 3, 1
    ABS_X, ABS_Y = 0, 1
    BTN_TOUCH = 330

    def __init__(self, on_tap):
        super().__init__(daemon=True)
        self.on_tap    = on_tap
        self._x        = 0
        self._y        = 0
        self._pressing = False

    def _find_device(self):
        try:
            info = Path("/proc/bus/input/devices").read_text()
            name = ""
            for line in info.splitlines():
                if line.startswith("N: Name="):
                    name = line
                if "ads7846" in name.lower() or "xpt2046" in name.lower():
                    if line.startswith("H: Handlers="):
                        for part in line.split():
                            if part.startswith("event"):
                                return f"/dev/input/{part}"
        except Exception:
            pass
        for i in range(4):
            p = f"/dev/input/event{i}"
            if os.path.exists(p):
                return p
        return None

    def run(self):
        device = self._find_device()
        if not device:
            log.warning("Touch: no device found")
            return
        log.info(f"Touch: using {device}")
        FMT  = "llHHi"
        SIZE = struct.calcsize(FMT)
        try:
            with open(device, "rb") as f:
                while True:
                    raw = f.read(SIZE)
                    if len(raw) < SIZE:
                        break
                    _, _, etype, code, value = struct.unpack(FMT, raw)
                    if etype == self.EV_ABS:
                        if code == self.ABS_X:
                            self._x = value
                        elif code == self.ABS_Y:
                            self._y = value
                    elif etype == self.EV_KEY and code == self.BTN_TOUCH:
                        self._pressing = (value == 1)
                    elif etype == self.EV_SYN and self._pressing:
                        nx = max(0.0, min(1.0, (self._x - TOUCH_X_MIN) / (TOUCH_X_MAX - TOUCH_X_MIN)))
                        ny = max(0.0, min(1.0, (self._y - TOUCH_Y_MIN) / (TOUCH_Y_MAX - TOUCH_Y_MIN)))
                        self._pressing = False
                        self.on_tap(nx, ny)
        except PermissionError:
            log.error("Touch: Permission denied — run: sudo usermod -aG input $USER && sudo reboot")
        except Exception as e:
            log.error(f"Touch error: {e}")


# ─── Display ──────────────────────────────────────────────────────────────────

class Display:

    def __init__(self, device_name: str):
        self.device_name = device_name
        self.recipient   = "miami" if device_name == "chile" else "chile"
        self.cell_w      = SCREEN_W // len(EMOJIS)

        self.font_sm = load_font(16)
        self.font_md = load_font(22)

        self.connected   = False
        self.last_emoji  = None   # stores index into EMOJIS
        self.last_sender = None
        self.last_ts     = None
        self.status_msg  = "Starting..."

        log.info("Loading emoji images...")
        self.emoji_sm = load_emoji_images(44)    # picker
        self.emoji_lg = load_emoji_images(140)   # received display
        self._lock = threading.Lock()

    def emoji_index_at(self, nx: float, ny: float):
        if ny >= (SCREEN_H - PICKER_H) / SCREEN_H:
            idx = int(nx * len(EMOJIS))
            return max(0, min(len(EMOJIS) - 1, idx))
        return None

    def render(self):
        canvas = Image.new("RGB", (SCREEN_W, SCREEN_H), C_BG)
        draw   = ImageDraw.Draw(canvas)
        with self._lock:
            self._draw_header(canvas, draw)
            self._draw_main(canvas, draw)
            self._draw_picker(canvas, draw)
        write_to_framebuffer(canvas)

    def _draw_header(self, canvas, draw):
        draw.rectangle([0, 0, SCREEN_W, HEADER_H], fill=(20, 10, 25))
        dot = C_GREEN if self.connected else C_RED
        draw.ellipse([8, 14, 22, 28], fill=dot)
        draw.text((30, 10), self.device_name.upper(), font=self.font_md, fill=C_ACCENT)

        now = datetime.now(timezone.utc)
        ch  = now.astimezone(TZ_CHILE).strftime("%H:%M")
        mi  = now.astimezone(TZ_MIAMI).strftime("%H:%M")
        clock_str = f"CL {ch}   US {mi}"
        bbox = draw.textbbox((0, 0), clock_str, font=self.font_sm)
        tw   = bbox[2] - bbox[0]
        draw.text((SCREEN_W - tw - 8, 14), clock_str, font=self.font_sm, fill=C_DIM)
        draw.line([0, HEADER_H, SCREEN_W, HEADER_H], fill=C_ACCENT, width=1)

    def _draw_main(self, canvas, draw):
        area_top = HEADER_H + 10
        area_bot = SCREEN_H - PICKER_H
        cy = (area_top + area_bot) // 2

        if self.last_emoji is not None:
            img = self.emoji_lg[self.last_emoji]
            paste_emoji(canvas, img, SCREEN_W // 2, cy - 10)
            label = f"from {self.last_sender}"
            bbox  = draw.textbbox((0, 0), label, font=self.font_sm)
            tw    = bbox[2] - bbox[0]
            draw.text(((SCREEN_W - tw) // 2, area_bot - 28),
                      label, font=self.font_sm, fill=C_DIM)
        else:
            msg  = self.status_msg if not self.connected else "Tap an emoji below to send"
            bbox = draw.textbbox((0, 0), msg, font=self.font_md)
            tw   = bbox[2] - bbox[0]
            draw.text(((SCREEN_W - tw) // 2, cy - 12),
                      msg, font=self.font_md, fill=C_DIM)

    def _draw_picker(self, canvas, draw):
        py = SCREEN_H - PICKER_H
        draw.rectangle([0, py, SCREEN_W, SCREEN_H], fill=C_PANEL)
        draw.line([0, py, SCREEN_W, py], fill=C_ACCENT, width=1)
        draw.text((6, py + 3), "SEND:", font=self.font_sm, fill=C_ACCENT)

        for i, img in enumerate(self.emoji_sm):
            cx = i * self.cell_w + self.cell_w // 2
            cy = py + PICKER_H // 2
            paste_emoji(canvas, img, cx, cy)
            if i > 0:
                draw.line([i * self.cell_w, py + 5,
                           i * self.cell_w, SCREEN_H - 5],
                          fill=(50, 40, 55), width=1)

    def set_connected(self, v):
        with self._lock:
            self.connected  = v
            self.status_msg = "Connected!" if v else "Reconnecting..."

    def set_received(self, emoji_char, sender):
        # Find index of received emoji char
        idx = None
        if emoji_char in EMOJI_CHARS:
            idx = EMOJI_CHARS.index(emoji_char)
        with self._lock:
            self.last_emoji  = idx
            self.last_sender = sender
            self.last_ts     = time.time()

    def set_status(self, msg):
        with self._lock:
            self.status_msg = msg


# ─── Main Client ──────────────────────────────────────────────────────────────

class ValentineClient:

    def __init__(self, device_name: str):
        self.device_name = device_name
        self.recipient   = "miami" if device_name == "chile" else "chile"
        self.display     = Display(device_name)

        self.sio = socketio.Client(
            reconnection=True,
            reconnection_attempts=0,
            reconnection_delay=3,
            reconnection_delay_max=15,
        )
        self._setup_events()

        self.touch = TouchReader(self._on_touch)
        self.touch.start()

    def _setup_events(self):

        @self.sio.on("connect")
        def on_connect():
            log.info("Connected to server")
            self.display.set_connected(True)
            self.sio.emit("register", {"device_name": self.device_name})

        @self.sio.on("registered")
        def on_registered(data):
            log.info(f"Registered: {data}")

        @self.sio.on("receive_emoji")
        def on_receive(data):
            emoji  = data.get("emoji", "?")
            sender = data.get("sender", "?")
            log.info(f"Received {emoji} from {sender}")
            self.display.set_received(emoji, sender)

        @self.sio.on("disconnect")
        def on_disconnect():
            log.warning("Disconnected from server")
            self.display.set_connected(False)

        @self.sio.on("ota_update")
        def on_ota(data):
            log.info("OTA update triggered")
            self.display.set_status("Updating...")
            os.system("cd ~/valentine-iot && git pull origin main")
            os.execv(sys.executable, [sys.executable] + sys.argv)

    def _on_touch(self, nx, ny):
        idx = self.display.emoji_index_at(nx, ny)
        if idx is not None and self.sio.connected:
            char = EMOJI_CHARS[idx]
            log.info(f"Sending {char} to {self.recipient}")
            try:
                self.sio.emit("send_emoji", {
                    "sender":    self.device_name,
                    "recipient": self.recipient,
                    "emoji":     char,
                })
            except Exception as e:
                log.error(f"Send error: {e}")

    def connect(self):
        self.display.set_status("Connecting...")
        self.display.render()
        while True:
            try:
                log.info(f"Connecting to {SERVER_URL}...")
                self.sio.connect(SERVER_URL, wait_timeout=10)
                break
            except Exception as e:
                log.warning(f"Failed: {e}, retrying in 5s...")
                self.display.set_status("Waiting for server...")
                self.display.render()
                time.sleep(5)

    def run(self):
        self.connect()
        log.info("Running!")
        last = 0.0

        while True:
            now = time.time()
            if now - last >= 0.5:
                try:
                    self.display.render()
                except Exception as e:
                    log.error(f"Render error: {e}")
                last = now

            # Auto-clear emoji after 30s
            if self.display.last_ts and now - self.display.last_ts > 30:
                with self.display._lock:
                    self.display.last_emoji  = None
                    self.display.last_sender = None
                    self.display.last_ts     = None

            time.sleep(0.1)


# ─── Entry Point ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--device", choices=["chile", "miami"], required=True)
    args = parser.parse_args()

    log.info(f"Valentine IoT v2 — {args.device.upper()}")

    if not Path(FRAMEBUFFER).exists():
        log.error(f"{FRAMEBUFFER} not found — is the TFT driver installed?")
        sys.exit(1)

    log.info("Downloading emoji PNGs (first run only)...")
    download_emoji_pngs()

    client = ValentineClient(args.device)
    try:
        client.run()
    except KeyboardInterrupt:
        log.info("Shutting down...")
        try:
            with open(FRAMEBUFFER, "wb") as fb:
                fb.write(b"\x00" * SCREEN_W * SCREEN_H * 2)
        except Exception:
            pass


if __name__ == "__main__":
    main()
