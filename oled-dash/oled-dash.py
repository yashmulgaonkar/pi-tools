#!/usr/bin/env python3
"""oled-dash — system stats on a 128x32 SSD1306."""

import math
import os
import signal
import socket
import time
from datetime import timedelta

import adafruit_ssd1306
import busio
import psutil
from board import SCL, SDA
from PIL import Image, ImageDraw, ImageFont

# --- Config ---
REFRESH_SECONDS = 5
CPU_SAMPLE_SECONDS = 0.25
LED_FRAME_DT = 1.0 / 20
SPLASH_SECONDS = 2.0
SHUTDOWN_SECONDS = 1.5
WIDTH, HEIGHT = 128, 32
MARGIN = 2
FAN_SIZE = 9
ACTIVITY_LED_R = 4  # ~9px — aligns with Terminus 12 row height
LOGO_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ym-logo.png")

_shutdown_requested = False

# Terminus 12 — crisp bitmap font for 1-bit OLEDs
FONT_PATH = "/usr/share/fonts/X11/misc/ter-u12n_unicode.pcf.gz"
FONT_BOLD_PATH = "/usr/share/fonts/X11/misc/ter-u12b_unicode.pcf.gz"
FONT_SIZE = 12

# --- Display init ---
i2c = busio.I2C(SCL, SDA)
disp = adafruit_ssd1306.SSD1306_I2C(WIDTH, HEIGHT, i2c)
disp.rotation = 2
disp.fill(0)
disp.show()

image = Image.new("1", (WIDTH, HEIGHT))
draw = ImageDraw.Draw(image)

font_meta = ImageFont.truetype(FONT_PATH, FONT_SIZE)
font_metric = font_meta
font_host = ImageFont.truetype(FONT_BOLD_PATH, FONT_SIZE)

_fan_hwmon = None
_prev_mmc_writes = None
_disk_led_until = 0.0
HOST_SCROLL_PX_PER_S = 24
HOST_SCROLL_GAP = 16  # pixels between end and restart of marquee


def clear():
    draw.rectangle((0, 0, WIDTH, HEIGHT), outline=0, fill=0)


def show():
    disp.image(image)
    disp.show()


def text_width(text, font):
    box = draw.textbbox((0, 0), text, font=font)
    return box[2] - box[0]


def draw_bold_text(x, y, text, font):
    """Bold Terminus + 1px shadow for extra weight on 1-bit OLED."""
    draw.text((x, y), text, font=font, fill=255)
    draw.text((x + 1, y), text, font=font, fill=255)


def draw_scrolling_host(x, y, max_w, text, font, now):
    """Draw bold hostname; marquee-scroll if wider than max_w."""
    if max_w <= 0:
        return
    tw = text_width(text, font) + 1  # +1 for bold shadow
    if tw <= max_w:
        draw_bold_text(x, y, text, font)
        return

    period = tw + HOST_SCROLL_GAP
    offset = int(now * HOST_SCROLL_PX_PER_S) % period
    strip = Image.new("1", (max_w, FONT_SIZE + 1), 0)
    sdraw = ImageDraw.Draw(strip)
    for ox in (-offset, -offset + period):
        sdraw.text((ox, 0), text, font=font, fill=255)
        sdraw.text((ox + 1, 0), text, font=font, fill=255)
    image.paste(strip, (x, y))


def draw_fan_icon(x, y, frame):
    cx = x + FAN_SIZE // 2
    cy = y + FAN_SIZE // 2
    arm = FAN_SIZE // 2
    angle = (frame % 24) * (math.pi / 12)
    for i in range(2):
        a = angle + i * (math.pi / 2)
        dx = arm * math.cos(a)
        dy = arm * math.sin(a)
        draw.line((cx - dx, cy - dy, cx + dx, cy + dy), fill=255)
    draw.point((cx, cy), fill=255)


def draw_activity_led(x, y, lit):
    r = ACTIVITY_LED_R
    bbox = (x, y, x + 2 * r, y + 2 * r)
    draw.ellipse(bbox, outline=255, fill=255 if lit else 0)


def draw_disk_led(x, y, lit):
    s = 2 * ACTIVITY_LED_R
    bbox = (x, y, x + s, y + s)
    draw.rectangle(bbox, outline=255, fill=255 if lit else 0)


def cpu_activity_lit(cpu_percent, now):
    load = max(0.0, min(100.0, float(cpu_percent))) / 100.0
    if load < 0.03:
        return False
    hz = 3.0 + load * 12.0
    return (now * hz) % 1.0 < max(0.12, load)


def mmc_write_bytes():
    try:
        per = psutil.disk_io_counters(perdisk=True) or {}
        if "mmcblk0" in per:
            return per["mmcblk0"].write_bytes
        total = 0
        found = False
        for name, io in per.items():
            if name.startswith(("ram", "loop", "zram")):
                continue
            total += io.write_bytes
            found = True
        return total if found else None
    except (OSError, AttributeError):
        return None


def disk_activity_lit(now):
    global _prev_mmc_writes, _disk_led_until
    wb = mmc_write_bytes()
    if wb is None:
        return False
    if _prev_mmc_writes is not None and wb > _prev_mmc_writes:
        delta = wb - _prev_mmc_writes
        _disk_led_until = now + 0.12 + min(0.35, delta / (512 * 1024))
    _prev_mmc_writes = wb
    if now >= _disk_led_until:
        return False
    return (now * 16.0) % 1.0 < 0.55


def fan_rpm():
    global _fan_hwmon
    try:
        if _fan_hwmon is None:
            for entry in os.listdir("/sys/class/hwmon"):
                base = f"/sys/class/hwmon/{entry}"
                try:
                    with open(f"{base}/name") as f:
                        if f.read().strip() == "pwmfan":
                            _fan_hwmon = base
                            break
                except OSError:
                    continue
        if _fan_hwmon is None:
            return 0
        with open(f"{_fan_hwmon}/fan1_input") as f:
            return int(f.read().strip())
    except (FileNotFoundError, ValueError, OSError):
        _fan_hwmon = None
        return 0


def primary_ip():
    addrs = psutil.net_if_addrs()
    for iface in ("wlan0", "eth0"):
        if iface not in addrs:
            continue
        for snic in addrs[iface]:
            if snic.family == socket.AF_INET:
                return snic.address
    for iface, snics in addrs.items():
        if iface == "lo":
            continue
        for snic in snics:
            if snic.family == socket.AF_INET:
                return snic.address
    return "—"


def cpu_temp_c():
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            return int(f.read().strip()) / 1000.0
    except OSError:
        return None


def format_uptime():
    delta = timedelta(seconds=int(time.time() - psutil.boot_time()))
    days = delta.days
    hours, rem = divmod(delta.seconds, 3600)
    mins = rem // 60
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {mins}m"
    return f"{mins}m"


def gather_stats():
    return {
        "name": socket.gethostname(),
        "ip": primary_ip(),
        "cpu": psutil.cpu_percent(interval=None),
        "mem": psutil.virtual_memory(),
        "disk": psutil.disk_usage("/"),
        "temp": cpu_temp_c(),
        "up": format_uptime(),
    }


def render(stats, fan_frame, fan_on, cpu_led_on, disk_led_on, now):
    clear()
    left = MARGIN
    right = WIDTH - MARGIN - 1

    cpu = stats["cpu"]
    mem = stats["mem"]
    disk = stats["disk"]
    temp = stats["temp"]

    # Right cluster first so we know how much room the hostname has
    cursor = right + 1
    led_d = 2 * ACTIVITY_LED_R + 1
    led_y = max(0, (FONT_SIZE - led_d) // 2)
    cursor -= led_d
    draw_activity_led(cursor, led_y, cpu_led_on)
    cursor -= 2 + led_d
    draw_disk_led(cursor, led_y, disk_led_on)
    cursor -= 2

    if temp is not None:
        t = f"{temp:.0f}C"
        cursor -= text_width(t, font_meta)
        draw.text((cursor, 0), t, font=font_meta, fill=255)
    if fan_on:
        cursor -= FAN_SIZE + 2
        draw_fan_icon(cursor, 1, fan_frame)

    host_max_w = max(0, cursor - left - 4)
    draw_scrolling_host(left, 0, host_max_w, stats["name"], font_host, now)

    draw.text((left, 11), stats["ip"], font=font_meta, fill=255)
    up = stats["up"]
    draw.text((right - text_width(up, font_meta) + 1, 11), up, font=font_meta, fill=255)

    # Fixed columns so digits don't shift labels
    col_w = (right - left + 1) // 3
    for i, (tag, pct) in enumerate(
        (("C", cpu), ("M", mem.percent), ("D", disk.percent))
    ):
        draw.text(
            (left + i * col_w, 22),
            f"{tag} {pct:3.0f}%",
            font=font_metric,
            fill=255,
        )


def splash():
    clear()
    try:
        logo = Image.open(LOGO_PATH).convert("RGBA")
        bg = Image.new("RGBA", logo.size, (0, 0, 0, 255))
        logo = Image.alpha_composite(bg, logo).convert("L")
        target_h = HEIGHT - 2
        target_w = max(1, int(logo.width * target_h / logo.height))
        if target_w > WIDTH - 2:
            target_w = WIDTH - 2
            target_h = max(1, int(logo.height * target_w / logo.width))
        logo = logo.resize((target_w, target_h), Image.Resampling.LANCZOS)
        logo = logo.point(lambda p: 255 if p > 128 else 0).convert("1")
        image.paste(logo, ((WIDTH - target_w) // 2, (HEIGHT - target_h) // 2))
    except OSError:
        draw.text((MARGIN, 10), "YM", font=font_host, fill=255)
    show()
    time.sleep(SPLASH_SECONDS)


def request_shutdown(signum, frame):
    global _shutdown_requested
    _shutdown_requested = True


def shutdown_display():
    """Show a brief message, then blank the OLED so it doesn't freeze on last frame."""
    try:
        clear()
        msg = "Shutting down..."
        tw = text_width(msg, font_meta)
        x = max(0, (WIDTH - tw) // 2)
        y = max(0, (HEIGHT - FONT_SIZE) // 2)
        draw.text((x, y), msg, font=font_meta, fill=255)
        show()
        time.sleep(SHUTDOWN_SECONDS)
        clear()
        show()
    except OSError:
        pass


def main():
    psutil.cpu_percent(interval=None)
    splash()
    stats = gather_stats()
    last_stats = time.monotonic()
    last_cpu = last_stats
    fan_frame = 0

    while not _shutdown_requested:
        t0 = time.monotonic()
        fan_on = fan_rpm() > 0

        if t0 - last_stats >= REFRESH_SECONDS:
            stats = gather_stats()
            last_stats = t0
            last_cpu = t0
        elif t0 - last_cpu >= CPU_SAMPLE_SECONDS:
            stats["cpu"] = psutil.cpu_percent(interval=None)
            stats["temp"] = cpu_temp_c()
            last_cpu = t0

        render(
            stats,
            fan_frame,
            fan_on,
            cpu_activity_lit(stats["cpu"], t0),
            disk_activity_lit(t0),
            t0,
        )
        show()

        # Keep animating for fan icon and/or hostname marquee
        fan_frame = fan_frame + 1 if fan_on else 0
        time.sleep(max(0.0, LED_FRAME_DT - (time.monotonic() - t0)))


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, request_shutdown)
    signal.signal(signal.SIGINT, request_shutdown)
    try:
        main()
    finally:
        shutdown_display()
