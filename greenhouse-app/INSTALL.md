# Greenhouse Monitor — Installation & Configuration

The steps below assume the files in this repository have been copied to the
project directory on your Raspberry Pi (for example `~/GreenHouse`). If you use
a different directory or user, adjust the paths in `deploy/*.service` and in
`start_greenhouse.sh` accordingly.

## 1. Dependencies

```bash
cd ~/GreenHouse
pip install -r requirements.txt --break-system-packages
```

## 2. Database

Create the performance index (if the table already exists, the command is
idempotent and can be run at any time):

```bash
psql -U <db_user> -d greenhouse -f schema.sql
```

## 3. Configure `.env`

```bash
cp .env.example .env
nano .env
```

Required values:

- `DB_PASSWORD` — your PostgreSQL password
- `FLASK_SECRET` — generate with `python3 -c "import secrets; print(secrets.token_hex(32))"`
- `GH_PASS_HASH` — generate with `python3 generate_hash.py` (prompts for a new dashboard password and prints the hash)

> **Important:** the application **refuses to start** without these three values —
> there are no default passwords in the code. Make sure `.env` is listed in
> `.gitignore` before pushing the project to a remote.

## 4. Run as systemd services (recommended)

```bash
# GPIO/SPI access without root for the service user
sudo usermod -aG gpio,spi <user>
sudo cp deploy/*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now greenhouse-monitor greenhouse-display greenhouse-web
```

Status and logs:

```bash
systemctl status greenhouse-web
journalctl -u greenhouse-monitor -f
```

The services start automatically at boot and restart on their own within a few
seconds (max 5 s) if a script crashes.

For quick tests without systemd, use `./start_greenhouse.sh`.

## 5. Features & Design Notes

**Security**

- credentials are loaded exclusively from `.env`; no passwords are hard-coded
- the dashboard password is stored as a hash (werkzeug), never in plaintext
- rate limiting on `/login`: max 8 attempts per 5 minutes per IP
- all data endpoints require authentication (`/api/timeseries`, `/api/heatmap`,
  and `/api/history` were previously public)
- the SQL interval in `timeseries()` is passed as a parameter (`%s::interval`),
  not interpolated into the query

**Robustness**

- a real WSGI server (gunicorn, 1 worker × 4 threads) instead of the Flask
  development server
- a PostgreSQL connection pool (`ThreadedConnectionPool`) instead of opening a
  new connection on every request
- a composite index `(sensor_type, timestamp DESC)` on the readings table
- systemd services with automatic restart and logging to the journal

**Features**

- offline sensor detection: if no climate average has been written for more than
  15 minutes (configurable via `CLIMATE_OFFLINE_SEC`), the "live transmission"
  badge in the topbar turns red and shows how many minutes of data are missing
- "Alert history" panel: fire/gas events from the last 90 days, reconstructed
  from 1→0 transitions, with start time and duration; alerts that are still
  active are highlighted as "ongoing"
- "Download CSV" button: exports temperature and humidity for the selected
  chart range (1 day / 1 month / 3 months)
- "Outside vs. Inside" band: local weather from Open-Meteo (no API key, cached
  10 minutes server-side) shown next to the current greenhouse values;
  coordinates are set in `.env` (`WEATHER_LAT`, `WEATHER_LON`)

## 6. Note on gunicorn and the alert thread

The thread that checks alerts (fire/gas → buzzer + Telegram) starts once, at
module import. For this reason the web service runs with a **single worker**
(`-w 1 --threads 4`); with multiple workers, alerts would be sent in duplicate.
