# oled-dash

128×32 SSD1306 status display for Raspberry Pi 5 (I2C).

## What it shows

```
hostname (bold, scrolls if long)   [fan] temp  □ ●
IP address                              uptime
C  nn%     M  nn%     D  nn%
```

- **Hostname** — Terminus Bold 12; marquees when it doesn’t fit beside temp/LEDs  
- **Temp** — CPU thermal zone (`°C` as `48C`)  
- **□** — SD/eMMC write activity (`mmcblk0`)  
- **●** — CPU activity flicker (duty ~ load)  
- **Fan** — rotating `+` when the active cooler RPM > 0 (starts ~50°C on stock Pi 5 curve)  
- **IP / uptime** — primary address + short uptime  
- **C / M / D** — CPU, memory, disk % in fixed columns (no progress bars)

Splash: `ym-logo.png` (YM monogram) for 2 seconds at start.

## Requirements

- Raspberry Pi OS with I2C enabled  
- SSD1306 128×32 on I2C (default bus; display rotated 180° in code)  
- Python 3.11+ recommended  

## Setup

```bash
sudo apt install -y python3-venv python3-pil python3-psutil xfonts-terminus i2c-tools
python3 -m venv --system-site-packages oled-dash-venv
source oled-dash-venv/bin/activate
pip install -r requirements.txt
```

Fonts: **Terminus 12** regular + bold (`ter-u12n_unicode` / `ter-u12b_unicode` from `xfonts-terminus`).

Keep `ym-logo.png` next to `oled-dash.py`.

## Run

```bash
./oled-dash-venv/bin/python oled-dash.py
```

Check the OLED is on the bus (often `0x3c`):

```bash
sudo i2cdetect -y 1
```

## Autostart (systemd)

The unit runs `/home/pi/pi-tools/oled-dash/oled-dash.py` with `/home/pi/oled-dash-venv`. Edit those paths if yours differ, then:

```bash
sudo cp oled-dash.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now oled-dash
```

Useful commands:

```bash
sudo systemctl status oled-dash
sudo systemctl restart oled-dash
sudo systemctl stop oled-dash
```

## Notes

- Pi 5: use system `python3-rpi-lgpio` via `--system-site-packages` if you add GPIO later; this app does **not** drive a physical LED.  
- Fan thresholds are the kernel pwm-fan trip points (typically 50 / 60 / 67.5 / 75 °C).

## License

Copyright (c) 2026 Yash Mulgaonkar.

Licensed under [CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/) — see the repository [LICENSE](../LICENSE).
