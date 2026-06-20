# Installation Guide — Greenhouse Monitor

Complete setup for a fresh Raspberry Pi OS install (tested on Raspberry Pi 5,
Raspberry Pi OS Bookworm).

## 1. System update & required packages

```bash
sudo apt update && sudo apt full-upgrade -y
sudo apt install -y git curl python3-pip python3-venv python3-dev \
    libpq-dev libgpiod2 i2c-tools postgresql postgresql-client
```

## 2. Enable SPI (for the TFT display)

```bash
sudo raspi-config
# -> 3 Interface Options -> I1 SPI -> Enable
```

Verify:
```bash
ls /dev/spidev*
# Expected: /dev/spidev0.0  /dev/spidev0.1
```

## 3. PostgreSQL setup

```bash
sudo systemctl enable postgresql
sudo systemctl start postgresql

sudo -u postgres psql -c "CREATE USER pi_user WITH PASSWORD 'change-me';"
sudo -u postgres psql -c "CREATE DATABASE greenhouse OWNER pi_user;"

psql -U pi_user -h localhost -d greenhouse -f schema.sql
```

## 4. Clone the repository & create the virtualenv

```bash
git clone https://github.com/your-username/greenhouse-monitor.git
cd greenhouse-monitor

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## 5. Configure environment variables

```bash
cp .env.example .env
nano .env
```

Generate the Flask secret key:
```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

Generate the dashboard password hash:
```bash
python3 generate_hash.py
```

Paste both values into `.env`.

## 6. Wire the hardware

See the pinout tables in `README.md` for the full GPIO mapping of every
sensor and the TFT display.

## 7. Test before enabling autostart

```bash
source venv/bin/activate
python3 greenhouse_monitor.py   # Ctrl+C to stop after confirming readings
python3 display_monitor.py      # Ctrl+C to stop after confirming the screen
python3 app.py                  # visit http://<pi-ip>:5000
```

## 8. Enable autostart with systemd

```bash
sudo cp deploy/*.service /etc/systemd/system/
sudo systemctl daemon-reload

sudo systemctl enable greenhouse-monitor greenhouse-web greenhouse-display
sudo systemctl start  greenhouse-monitor greenhouse-web greenhouse-display
```

Check status:
```bash
sudo systemctl status greenhouse-web
sudo journalctl -u greenhouse-web -f
```

## Troubleshooting

**`GPIO busy` on the TFT CS pin** — GPIO8/CE0 is claimed by the kernel SPI
driver; this is expected. The provided pinout (CS on GPIO5) avoids this.

**`function round(double precision, integer) does not exist`** — PostgreSQL
requires an explicit cast; already fixed in `app.py` via
`ROUND(AVG(value)::numeric, 2)`.

**DHT11 occasional read errors** — normal behavior for this sensor; the
3-attempt retry logic in `greenhouse_monitor.py` absorbs it.
