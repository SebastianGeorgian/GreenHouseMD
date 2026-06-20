"""
Greenhouse Monitor — Web Server (Flask)

Changes from the basic version:
  - credentials exclusively from .env (python-dotenv); app refuses to start without them
  - login password stored as a hash (werkzeug), not in plaintext
  - rate limiting on /login (anti brute-force), in addition to the existing one on /api/ingest
  - PostgreSQL connection pool (ThreadedConnectionPool) instead of a connection per request
  - parameterized SQL interval (%s::interval) instead of f-string
  - mandatory authentication on ALL data endpoints (including timeseries/heatmap)
  - /api/status includes the age of the last reading -> offline sensor/station detection
  - /api/alerts: fire/gas alert history with duration
  - /api/export: CSV download for the selected interval
  - /api/weather: outside weather (Open-Meteo, no API key, 10 min cache)
  - gunicorn-compatible (alert thread starts on import, only once)
"""

from flask import Flask, render_template, request, redirect, session, flash, jsonify, Response
import psycopg2
from psycopg2.pool import ThreadedConnectionPool
import pandas as pd
import threading
import time
import os
import sys
import requests
import logging
import io
from datetime import datetime
from collections import deque
from contextlib import contextmanager

from dotenv import load_dotenv
from werkzeug.security import check_password_hash
from functools import wraps

# ---------------- Config from .env ----------------
load_dotenv()  # looks for .env in the current directory / parents

def require_env(name: str) -> str:
    """Stops the application if a critical variable is missing — no hardcoded fallbacks."""
    val = os.getenv(name)
    if not val:
        print(f"ERROR: environment variable '{name}' is missing. "
              f"Copy .env.example to .env and fill it in.", file=sys.stderr)
        sys.exit(1)
    return val

DB_CONFIG = {
    "dbname":   os.getenv("DB_NAME", "greenhouse"),
    "user":     os.getenv("DB_USER", "pi_user"),
    "password": require_env("DB_PASSWORD"),
    "host":     os.getenv("DB_HOST", "localhost"),
}

FLASK_SECRET = require_env("FLASK_SECRET")
GH_USER      = require_env("GH_USER")
GH_PASS_HASH = require_env("GH_PASS_HASH")   # generated with generate_hash.py

TELEGRAM_TOKEN         = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID       = os.getenv("TELEGRAM_CHAT_ID", "")
ALERT_COOLDOWN_SECONDS = int(os.getenv("ALERT_COOLDOWN_SECONDS", "300"))
INGEST_API_KEY         = os.getenv("INGEST_API_KEY", "")

WEATHER_LAT = float(os.getenv("WEATHER_LAT", "44.18"))   # default: Constanta, RO
WEATHER_LON = float(os.getenv("WEATHER_LON", "28.65"))

# Threshold above which the climate sensor is considered offline.
# DHT writes the average every 6 min, so 15 min = ~2.5 missed windows.
CLIMATE_OFFLINE_SEC = int(os.getenv("CLIMATE_OFFLINE_SEC", "900"))

# ---------------- GPIO Setup ----------------
try:
    from gpiozero import Device, Buzzer
    from gpiozero.pins.mock import MockFactory
    if os.geteuid() != 0:
        Device.pin_factory = MockFactory()
except Exception:
    class Buzzer:
        def __init__(self, pin): pass
        def on(self): print("Buzzer ON")
        def off(self): print("Buzzer OFF")

buzzer = Buzzer(18)

# ---------------- Flask ----------------
app = Flask(__name__)
app.secret_key = FLASK_SECRET

# ---------------- Logging ----------------
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("greenhouse")

# ---------------- DB Pool ----------------
try:
    pool = ThreadedConnectionPool(minconn=1, maxconn=5, **DB_CONFIG)
except Exception as exc:
    print(f"ERROR: cannot connect to PostgreSQL: {exc}", file=sys.stderr)
    sys.exit(1)

@contextmanager
def db_conn():
    """Borrows a connection from the pool and guarantees it is returned."""
    conn = pool.getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)

# ---------------- Authentication ----------------
def login_required(fn):
    """Protects data endpoints. JSON 401 for API, redirect for pages."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("logged_in"):
            if request.path.startswith("/api/"):
                return jsonify({"error": "unauthorized"}), 401
            return redirect("/login")
        return fn(*args, **kwargs)
    return wrapper

# ---------------- Rate limiting (IP, no dependencies) ----------------
_rate_buckets = {}   # key -> deque[timestamps]
_rate_lock = threading.Lock()

def rate_limit_ok(key: str, window_sec: int, max_req: int) -> bool:
    now = time.time()
    with _rate_lock:
        q = _rate_buckets.get(key)
        if q is None:
            q = deque()
            _rate_buckets[key] = q
        while q and (now - q[0]) > window_sec:
            q.popleft()
        if len(q) >= max_req:
            return False
        q.append(now)
        return True

def client_ip() -> str:
    return request.headers.get("X-Forwarded-For", request.remote_addr) or "unknown"

# ingest limits (as before)
_RATE_WINDOW_SEC = int(os.getenv("INGEST_RATE_WINDOW_SEC", "10"))
_RATE_MAX_REQ    = int(os.getenv("INGEST_RATE_MAX_REQ", "60"))
# login limits: max 8 attempts / 5 minutes / IP
LOGIN_WINDOW_SEC = 300
LOGIN_MAX_REQ    = 8

# ---------------- Telegram ----------------
def send_telegram(msg: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.get(url, params={"chat_id": TELEGRAM_CHAT_ID, "text": msg}, timeout=5)
    except Exception as exc:
        logger.warning("Telegram send failed: %s", exc)

def buzzer_beep(sec=3):
    threading.Thread(target=lambda: (buzzer.on(), time.sleep(sec), buzzer.off()), daemon=True).start()

# ---------------- Alerts (fire / gas) ----------------
alerted = {}

def check_alerts():
    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT sensor_type, value, timestamp
            FROM sensor_readings
            WHERE sensor_type IN ('fire','gas')
            ORDER BY timestamp DESC
            LIMIT 10
        """)
        rows = cur.fetchall()
        cur.close()

    for s, v, t in rows:
        key = f"{s}_{t}"
        now = time.time()
        last_sent = alerted.get(s, 0)
        if v == 1 and key not in alerted and (now - last_sent) >= ALERT_COOLDOWN_SECONDS:
            buzzer_beep()
            send_telegram(f"ALERT: {s.upper()} detected in the greenhouse!")
            alerted[key] = now
            alerted[s] = now
            logger.warning("Alert triggered for %s at %s", s, t)

def alert_loop():
    while True:
        try:
            check_alerts()
        except Exception as exc:
            logger.error("alert_loop error: %s", exc)
        time.sleep(5)

# Starts only once, including under gunicorn (where __main__ doesn't run).
# Important: run gunicorn with a SINGLE worker (-w 1 --threads 4),
# otherwise the alert thread would run in every worker.
_alert_thread_started = False
_alert_thread_lock = threading.Lock()

def start_alert_thread():
    global _alert_thread_started
    with _alert_thread_lock:
        if not _alert_thread_started:
            threading.Thread(target=alert_loop, daemon=True, name="alerts").start()
            _alert_thread_started = True

start_alert_thread()

# ---------------- Data helpers ----------------
def last_value(sensor):
    with db_conn() as conn:
        df = pd.read_sql("""
            SELECT value
            FROM sensor_readings
            WHERE sensor_type=%s
            ORDER BY timestamp DESC
            LIMIT 1
        """, conn, params=(sensor,))
    if df.empty:
        return None
    return float(df["value"].iloc[0])

def last_reading_age(sensor=None):
    """Seconds since the last reading (a specific sensor or any sensor)."""
    with db_conn() as conn:
        if sensor:
            df = pd.read_sql("""
                SELECT EXTRACT(EPOCH FROM (NOW() - MAX(timestamp))) AS age
                FROM sensor_readings WHERE sensor_type=%s
            """, conn, params=(sensor,))
        else:
            df = pd.read_sql("""
                SELECT EXTRACT(EPOCH FROM (NOW() - MAX(timestamp))) AS age
                FROM sensor_readings
            """, conn)
    age = df["age"].iloc[0]
    return None if pd.isna(age) else float(age)

RANGES = {
    "day":     "1 day",
    "month":   "1 month",
    "3months": "3 months",
}

def timeseries(range_key: str):
    if range_key not in RANGES:
        range_key = "day"
    interval = RANGES[range_key]

    with db_conn() as conn:
        # interval passed as a parameter, not interpolated into the query
        temp = pd.read_sql("""
            SELECT timestamp, value
            FROM sensor_readings
            WHERE sensor_type='temperature_avg_6min'
              AND timestamp >= NOW() - %s::interval
            ORDER BY timestamp
        """, conn, params=(interval,))

        hum = pd.read_sql("""
            SELECT timestamp, value
            FROM sensor_readings
            WHERE sensor_type='humidity_avg_6min'
              AND timestamp >= NOW() - %s::interval
            ORDER BY timestamp
        """, conn, params=(interval,))

    temp["timestamp"] = pd.to_datetime(temp["timestamp"])
    hum["timestamp"] = pd.to_datetime(hum["timestamp"])

    labels = temp["timestamp"].dt.strftime("%d %b %H:%M").tolist()

    return {
        "labels": labels,
        "temperature": temp["value"].round(2).tolist(),
        "humidity": hum["value"].round(2).tolist(),
    }

def heatmap_daygrid(date_str: str, metric: str):
    if metric not in ("temperature_avg_6min", "humidity_avg_6min"):
        metric = "temperature_avg_6min"

    with db_conn() as conn:
        df = pd.read_sql("""
            SELECT EXTRACT(HOUR FROM timestamp)::int AS hour, ROUND(AVG(value)::numeric,2) AS avg
            FROM sensor_readings
            WHERE sensor_type=%s
              AND timestamp >= %s::date
              AND timestamp <  (%s::date + INTERVAL '1 day')
            GROUP BY hour
            ORDER BY hour
        """, conn, params=(metric, date_str, date_str))

    hour_to_val = {int(r["hour"]): float(r["avg"]) for _, r in df.iterrows()}
    values = [hour_to_val.get(h, None) for h in range(24)]

    return {
        "mode": "daygrid",
        "date": date_str,
        "metric": metric,
        "labels": [f"{h:02d}:00" for h in range(24)],
        "values": values
    }

def heatmap_monthgrid(month_str: str, metric: str):
    if metric not in ("temperature_avg_6min", "humidity_avg_6min"):
        metric = "temperature_avg_6min"

    start = f"{month_str}-01"
    with db_conn() as conn:
        df = pd.read_sql("""
            SELECT DATE(timestamp) AS day, ROUND(AVG(value)::numeric,2) AS avg
            FROM sensor_readings
            WHERE sensor_type=%s
              AND timestamp >= %s::date
              AND timestamp <  (DATE_TRUNC('month', %s::date) + INTERVAL '1 month')
            GROUP BY day
            ORDER BY day
        """, conn, params=(metric, start, start))

    return {
        "mode": "monthgrid",
        "month": month_str,
        "metric": metric,
        "days": df["day"].astype(str).tolist(),
        "values": df["avg"].tolist()
    }

def alert_history(limit=10, days=90):
    """
    Reconstructs fire/gas alert events from 1 -> 0 transitions,
    with the duration of each event. Still-open events have ongoing=True.
    """
    with db_conn() as conn:
        df = pd.read_sql("""
            SELECT sensor_type, value, timestamp
            FROM sensor_readings
            WHERE sensor_type IN ('fire','gas')
              AND timestamp >= NOW() - %s::interval
            ORDER BY timestamp ASC
        """, conn, params=(f"{days} days",))

    events = []
    open_event = {}   # sensor -> start timestamp

    for _, row in df.iterrows():
        s, v, t = row["sensor_type"], int(row["value"]), row["timestamp"]
        if v == 1 and s not in open_event:
            open_event[s] = t
        elif v == 0 and s in open_event:
            start = open_event.pop(s)
            events.append({
                "sensor": s,
                "start": start.strftime("%d %b %Y, %H:%M"),
                "duration_sec": int((t - start).total_seconds()),
                "ongoing": False,
            })

    # still-active events
    for s, start in open_event.items():
        events.append({
            "sensor": s,
            "start": start.strftime("%d %b %Y, %H:%M"),
            "duration_sec": max(0, int((pd.Timestamp.now() - start).total_seconds())),
            "ongoing": True,
        })

    events.sort(key=lambda e: e["start"], reverse=True)
    return events[:limit]

# ---------------- Outside weather (Open-Meteo, 10 min cache) ----------------
_weather_cache = {"ts": 0.0, "data": None}
_weather_lock = threading.Lock()

WMO_DESCRIPTIONS = {
    0: "clear sky", 1: "mostly clear", 2: "partly cloudy", 3: "overcast",
    45: "fog", 48: "rime fog",
    51: "light drizzle", 53: "drizzle", 55: "dense drizzle",
    61: "light rain", 63: "rain", 65: "heavy rain",
    66: "freezing rain", 67: "heavy freezing rain",
    71: "light snow", 73: "snow", 75: "heavy snow",
    80: "light showers", 81: "showers", 82: "heavy showers",
    95: "thunderstorm", 96: "thunderstorm with hail", 99: "thunderstorm with hail",
}

def get_weather():
    with _weather_lock:
        if _weather_cache["data"] and (time.time() - _weather_cache["ts"]) < 600:
            return _weather_cache["data"]
    try:
        r = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": WEATHER_LAT,
                "longitude": WEATHER_LON,
                "current": "temperature_2m,relative_humidity_2m,precipitation,weather_code",
                "timezone": "auto",
            },
            timeout=6,
        )
        r.raise_for_status()
        cur = r.json().get("current", {})
        data = {
            "temp": cur.get("temperature_2m"),
            "hum": cur.get("relative_humidity_2m"),
            "precip": cur.get("precipitation"),
            "desc": WMO_DESCRIPTIONS.get(cur.get("weather_code"), ""),
        }
        with _weather_lock:
            _weather_cache.update(ts=time.time(), data=data)
        return data
    except Exception as exc:
        logger.warning("Weather fetch failed: %s", exc)
        return None

# ---------------- Ingest (Pi -> Server) ----------------
def _parse_timestamp(ts):
    if not ts:
        return datetime.now()
    try:
        if isinstance(ts, str) and ts.endswith("Z"):
            return datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return datetime.fromisoformat(ts)
    except Exception:
        return datetime.now()

@app.route("/api/ingest", methods=["POST"])
def api_ingest():
    ip = client_ip()
    if not rate_limit_ok(f"ingest:{ip}", _RATE_WINDOW_SEC, _RATE_MAX_REQ):
        return jsonify({"error": "rate_limited"}), 429

    if INGEST_API_KEY:
        provided = request.headers.get("X-API-Key", "")
        if provided != INGEST_API_KEY:
            return jsonify({"error": "unauthorized"}), 401

    if not request.is_json:
        return jsonify({"error": "json_required"}), 400

    data = request.get_json(silent=True) or {}
    readings = data.get("readings")
    if readings is None:
        readings = [data]

    rows = []
    for r in readings:
        try:
            sensor = str(r["sensor"]).strip()
            value = float(r["value"])
            ts = _parse_timestamp(r.get("timestamp"))
            rows.append((sensor, value, ts))
        except Exception as exc:
            logger.warning("Bad ingest row from %s: %s | row=%s", ip, exc, r)
            return jsonify({"error": "bad_payload", "details": str(exc)}), 400

    if not rows:
        return jsonify({"error": "empty"}), 400

    try:
        with db_conn() as conn:
            cur = conn.cursor()
            cur.executemany(
                "INSERT INTO sensor_readings (sensor_type, value, timestamp) VALUES (%s, %s, %s)",
                rows
            )
            cur.close()
        return jsonify({"status": "ok", "inserted": len(rows)}), 201
    except Exception as exc:
        logger.exception("DB insert failed: %s", exc)
        return jsonify({"error": "db_error"}), 500

# ---------------- Page routes ----------------
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        ip = client_ip()
        if not rate_limit_ok(f"login:{ip}", LOGIN_WINDOW_SEC, LOGIN_MAX_REQ):
            flash("Too many attempts. Wait a few minutes and try again.")
            return render_template("login.html"), 429

        username = request.form.get("username", "")
        password = request.form.get("password", "")

        if username == GH_USER and check_password_hash(GH_PASS_HASH, password):
            session["logged_in"] = True
            return redirect("/")
        flash("Incorrect username or password.")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")

@app.route("/")
@login_required
def index():
    init = timeseries("day")
    today = datetime.now().strftime("%Y-%m-%d")
    hm = heatmap_daygrid(today, "temperature_avg_6min")

    return render_template(
        "index.html",
        init_labels=init["labels"],
        init_temp=init["temperature"],
        init_hum=init["humidity"],
        init_range="day",
        init_heatmap=hm,
        rain=int(last_value("rain") or 0),
        fire=int(last_value("fire") or 0),
        gas=int(last_value("gas") or 0),
        soil=int(last_value("soil") or 0),
    )

# ---------------- Protected API ----------------
@app.route("/api/timeseries")
@login_required
def api_timeseries():
    range_key = request.args.get("range", "day")
    return jsonify(timeseries(range_key))

@app.route("/api/heatmap")
@login_required
def api_heatmap():
    mode = request.args.get("mode", "daygrid")
    metric = request.args.get("metric", "temperature_avg_6min")
    date = request.args.get("date")
    month = request.args.get("month")

    if mode == "monthgrid":
        if not month:
            month = datetime.now().strftime("%Y-%m")
        return jsonify(heatmap_monthgrid(month, metric))

    if not date:
        date = datetime.now().strftime("%Y-%m-%d")
    return jsonify(heatmap_daygrid(date, metric))

@app.route("/api/history")
@login_required
def api_history():
    date = request.args["date"]
    start = int(request.args.get("start", 0))
    hours = int(request.args.get("hours", 3))
    start_ts = f"{date} {start:02d}:00:00"
    end_ts = f"{date} {start+hours:02d}:00:00"

    with db_conn() as conn:
        df = pd.read_sql("""
            SELECT timestamp, value
            FROM sensor_readings
            WHERE sensor_type='temperature_avg_6min'
              AND timestamp BETWEEN %s AND %s
            ORDER BY timestamp
        """, conn, params=(start_ts, end_ts))

    return jsonify({
        "labels": pd.to_datetime(df["timestamp"]).dt.strftime("%H:%M").tolist(),
        "values": df["value"].round(2).tolist()
    })

@app.route("/api/events")
@login_required
def api_events():
    sensor = request.args.get("sensor", "rain")
    if sensor not in ("rain", "soil", "fire", "gas"):
        return jsonify({"error": "invalid sensor"}), 400

    start = request.args.get("start")
    end = request.args.get("end")

    if not start:
        start = datetime.now().strftime("%Y-%m-%d")
    if not end:
        end = datetime.now().strftime("%Y-%m-%d")

    from datetime import date as dt_date
    try:
        start_d = dt_date.fromisoformat(start)
        end_d = dt_date.fromisoformat(end)
        days = (end_d - start_d).days
    except Exception:
        days = 0

    with db_conn() as conn:
        if days <= 1:
            df = pd.read_sql("""
                SELECT timestamp, value
                FROM sensor_readings
                WHERE sensor_type = %s
                  AND timestamp >= %s::date
                  AND timestamp <  (%s::date + INTERVAL '1 day')
                ORDER BY timestamp
            """, conn, params=(sensor, start, start))

            df["timestamp"] = pd.to_datetime(df["timestamp"])
            labels = df["timestamp"].dt.strftime("%H:%M").tolist()
            values = df["value"].astype(int).tolist()
        else:
            df = pd.read_sql("""
                SELECT
                    DATE_TRUNC('hour', timestamp) AS hour,
                    MAX(value::int) AS value
                FROM sensor_readings
                WHERE sensor_type = %s
                  AND timestamp >= %s::date
                  AND timestamp <  %s::date + INTERVAL '1 day'
                GROUP BY hour
                ORDER BY hour
            """, conn, params=(sensor, start, end))

            df["hour"] = pd.to_datetime(df["hour"])
            if days <= 7:
                labels = df["hour"].dt.strftime("%d %b %H:%M").tolist()
            else:
                labels = df["hour"].dt.strftime("%d %b").tolist()
            values = df["value"].astype(int).tolist()

    return jsonify({"labels": labels, "values": values})

@app.route("/api/status")
@login_required
def api_status():
    climate_age = last_reading_age("temperature_avg_6min")
    any_age = last_reading_age()
    return jsonify({
        "rain": int(last_value("rain") or 0),
        "fire": int(last_value("fire") or 0),
        "gas":  int(last_value("gas") or 0),
        "soil": int(last_value("soil") or 0),
        "temp": last_value("temperature_avg_6min"),
        "hum":  last_value("humidity_avg_6min"),
        "climate_age_sec": None if climate_age is None else round(climate_age),
        "any_age_sec":     None if any_age is None else round(any_age),
        "climate_offline": climate_age is None or climate_age > CLIMATE_OFFLINE_SEC,
    })

@app.route("/api/alerts")
@login_required
def api_alerts():
    try:
        limit = min(int(request.args.get("limit", 10)), 50)
    except ValueError:
        limit = 10
    return jsonify({"alerts": alert_history(limit=limit)})

@app.route("/api/weather")
@login_required
def api_weather():
    data = get_weather()
    if data is None:
        return jsonify({"error": "weather_unavailable"}), 503
    return jsonify(data)

@app.route("/api/export")
@login_required
def api_export():
    """Exports temperature + humidity for the selected interval as a CSV file."""
    range_key = request.args.get("range", "day")
    if range_key not in RANGES:
        range_key = "day"
    interval = RANGES[range_key]

    with db_conn() as conn:
        df = pd.read_sql("""
            SELECT timestamp, sensor_type, value
            FROM sensor_readings
            WHERE sensor_type IN ('temperature_avg_6min','humidity_avg_6min')
              AND timestamp >= NOW() - %s::interval
            ORDER BY timestamp
        """, conn, params=(interval,))

    if df.empty:
        pivot = pd.DataFrame(columns=["timestamp", "temperature_c", "humidity_pct"])
    else:
        pivot = df.pivot_table(index="timestamp", columns="sensor_type",
                               values="value", aggfunc="first").reset_index()
        pivot = pivot.rename(columns={
            "temperature_avg_6min": "temperature_c",
            "humidity_avg_6min": "humidity_pct",
        })

    buf = io.StringIO()
    pivot.to_csv(buf, index=False)
    filename = f"greenhouse_{range_key}_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"

    return Response(
        buf.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )

# ---------------- Main (development only) ----------------
if __name__ == "__main__":
    # In production use gunicorn (see deploy/greenhouse-web.service):
    #   gunicorn -w 1 --threads 4 -b 0.0.0.0:5000 app:app
    app.run(host="0.0.0.0", port=5000)
