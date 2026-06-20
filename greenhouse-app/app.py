"""
Greenhouse Monitor — server web (Flask)

Modificari fata de versiunea anterioara:
  - credentiale exclusiv din .env (python-dotenv); aplicatia refuza sa porneasca fara ele
  - parola de login stocata ca hash (werkzeug), nu in clar
  - rate limiting pe /login (anti brute-force), pe langa cel existent pe /api/ingest
  - pool de conexiuni PostgreSQL (ThreadedConnectionPool) in loc de conexiune per-request
  - interval SQL parametrizat (%s::interval) in loc de f-string
  - autentificare obligatorie pe TOATE endpoint-urile de date (inclusiv timeseries/heatmap)
  - /api/status include varsta ultimei citiri -> detectare senzor/statie offline
  - /api/alerts: istoric alerte foc/gaz cu durata
  - /api/export: descarcare CSV pentru intervalul selectat
  - /api/weather: vremea de afara (Open-Meteo, fara cheie API, cache 10 min)
  - compatibil gunicorn (thread-ul de alerte porneste la import, o singura data)
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

# ---------------- Config din .env ----------------
load_dotenv()  # cauta .env in directorul curent / parinti

def require_env(name: str) -> str:
    """Opreste aplicatia daca o variabila critica lipseste — fara fallback-uri hardcodate."""
    val = os.getenv(name)
    if not val:
        print(f"EROARE: variabila de mediu '{name}' lipseste. "
              f"Copiaza .env.example in .env si completeaz-o.", file=sys.stderr)
        sys.exit(1)
    return val

DB_CONFIG = {
    "dbname":   os.getenv("DB_NAME", "greenhouse"),
    "user":     os.getenv("DB_USER", "pi_sebastian"),
    "password": require_env("DB_PASSWORD"),
    "host":     os.getenv("DB_HOST", "localhost"),
}

FLASK_SECRET = require_env("FLASK_SECRET")
GH_USER      = require_env("GH_USER")
GH_PASS_HASH = require_env("GH_PASS_HASH")   # generat cu generate_hash.py

TELEGRAM_TOKEN         = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID       = os.getenv("TELEGRAM_CHAT_ID", "")
ALERT_COOLDOWN_SECONDS = int(os.getenv("ALERT_COOLDOWN_SECONDS", "300"))
INGEST_API_KEY         = os.getenv("INGEST_API_KEY", "")

WEATHER_LAT = float(os.getenv("WEATHER_LAT", "44.18"))   # Constanta
WEATHER_LON = float(os.getenv("WEATHER_LON", "28.65"))

# Pragul peste care senzorul de clima e considerat offline.
# DHT scrie media la 6 min, deci 15 min = ~2.5 ferestre ratate.
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

# ---------------- Pool DB ----------------
try:
    pool = ThreadedConnectionPool(minconn=1, maxconn=5, **DB_CONFIG)
except Exception as exc:
    print(f"EROARE: nu ma pot conecta la PostgreSQL: {exc}", file=sys.stderr)
    sys.exit(1)

@contextmanager
def db_conn():
    """Imprumuta o conexiune din pool si o returneaza garantat."""
    conn = pool.getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)

# ---------------- Autentificare ----------------
def login_required(fn):
    """Protejeaza endpoint-urile de date. JSON 401 pentru API, redirect pentru pagini."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("logged_in"):
            if request.path.startswith("/api/"):
                return jsonify({"error": "unauthorized"}), 401
            return redirect("/login")
        return fn(*args, **kwargs)
    return wrapper

# ---------------- Rate limiting (IP, fara dependente) ----------------
_rate_buckets = {}   # cheie -> deque[timestamps]
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

# limite ingest (ca inainte)
_RATE_WINDOW_SEC = int(os.getenv("INGEST_RATE_WINDOW_SEC", "10"))
_RATE_MAX_REQ    = int(os.getenv("INGEST_RATE_MAX_REQ", "60"))
# limite login: max 8 incercari / 5 minute / IP
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

# ---------------- Alerte (foc / gaz) ----------------
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
            send_telegram(f"ALERTA: {s.upper()} detectat in sera!")
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

# Porneste o singura data, inclusiv sub gunicorn (unde __main__ nu ruleaza).
# Important: ruleaza gunicorn cu UN singur worker (-w 1 --threads 4),
# altfel thread-ul de alerte ar rula in fiecare worker.
_alert_thread_started = False
_alert_thread_lock = threading.Lock()

def start_alert_thread():
    global _alert_thread_started
    with _alert_thread_lock:
        if not _alert_thread_started:
            threading.Thread(target=alert_loop, daemon=True, name="alerts").start()
            _alert_thread_started = True

start_alert_thread()

# ---------------- Helpers date ----------------
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
    """Secunde de la ultima citire (a unui senzor anume sau a oricarui senzor)."""
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
        # interval transmis ca parametru, nu interpolat in query
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
    Reconstruieste evenimentele de alerta (foc/gaz) din tranzitiile 1 -> 0,
    cu durata fiecarui eveniment. Evenimentele inca deschise au ongoing=True.
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

    # evenimente inca active
    for s, start in open_event.items():
        events.append({
            "sensor": s,
            "start": start.strftime("%d %b %Y, %H:%M"),
            "duration_sec": max(0, int((pd.Timestamp.now() - start).total_seconds())),
            "ongoing": True,
        })

    events.sort(key=lambda e: e["start"], reverse=True)
    return events[:limit]

# ---------------- Vremea de afara (Open-Meteo, cache 10 min) ----------------
_weather_cache = {"ts": 0.0, "data": None}
_weather_lock = threading.Lock()

WMO_RO = {
    0: "senin", 1: "predominant senin", 2: "partial noros", 3: "noros",
    45: "ceata", 48: "ceata cu chiciura",
    51: "burnita slaba", 53: "burnita", 55: "burnita densa",
    61: "ploaie slaba", 63: "ploaie", 65: "ploaie puternica",
    66: "ploaie inghetata", 67: "ploaie inghetata puternica",
    71: "ninsoare slaba", 73: "ninsoare", 75: "ninsoare puternica",
    80: "averse slabe", 81: "averse", 82: "averse puternice",
    95: "furtuna", 96: "furtuna cu grindina", 99: "furtuna cu grindina",
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
            "desc": WMO_RO.get(cur.get("weather_code"), ""),
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

# ---------------- Rute pagini ----------------
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        ip = client_ip()
        if not rate_limit_ok(f"login:{ip}", LOGIN_WINDOW_SEC, LOGIN_MAX_REQ):
            flash("Prea multe incercari. Asteapta cateva minute si incearca din nou.")
            return render_template("login.html"), 429

        username = request.form.get("username", "")
        password = request.form.get("password", "")

        if username == GH_USER and check_password_hash(GH_PASS_HASH, password):
            session["logged_in"] = True
            return redirect("/")
        flash("Utilizator sau parola incorecte.")
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

# ---------------- API protejat ----------------
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
    """Exporta temperatura + umiditatea din intervalul selectat ca fisier CSV."""
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

# ---------------- Main (doar pentru dezvoltare) ----------------
if __name__ == "__main__":
    # In productie foloseste gunicorn (vezi deploy/greenhouse-web.service):
    #   gunicorn -w 1 --threads 4 -b 0.0.0.0:5000 app:app
    app.run(host="0.0.0.0", port=5000)
