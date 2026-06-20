#!/usr/bin/env python3
"""
Greenhouse Monitor — Pi5
Real DHT11 sensor + digital sensors (rain/fire/gas/soil) + buzzer

Sensors:
  DHT11        — temperature + humidity      GPIO4  (pin 7)
  Rain         — precipitation detection     GPIO17 (pin 11)
  Buzzer       — audible alert                GPIO18 (pin 12)
  Fire (IR)    — flame detection              GPIO27 (pin 13)
  Gas MQ-6     — LPG / butane / propane       GPIO22 (pin 15)
  Soil         — soil moisture                GPIO23 (pin 16)

Dependencies:
  pip install adafruit-circuitpython-dht gpiozero psycopg2-binary RPi.GPIO
"""

import time
import threading
import os
import collections
from datetime import datetime

import board
import adafruit_dht
from gpiozero import Button, Buzzer
import psycopg2

# ─────────────────────────────────────────────
# CONFIGURATION — exclusively from .env, no hardcoded passwords
# ─────────────────────────────────────────────
from dotenv import load_dotenv
load_dotenv()

def require_env(name):
    val = os.getenv(name)
    if not val:
        print(f"ERROR: variable '{name}' is missing from .env. "
              f"Copy .env.example to .env and fill it in.")
        raise SystemExit(1)
    return val

DB_NAME = os.getenv("DB_NAME", "greenhouse")
DB_USER = os.getenv("DB_USER", "pi_user")
DB_PASS = require_env("DB_PASSWORD")
DB_HOST = os.getenv("DB_HOST", "localhost")

# BCM pins
DHT_PIN    = board.D4   # GPIO4
RAIN_PIN   = 17         # GPIO17
FIRE_PIN   = 27         # GPIO27
GAS_PIN    = 22         # GPIO22 — MQ-6
SOIL_PIN   = 23         # GPIO23
BUZZER_PIN = 18         # GPIO18

# Alert thresholds
TEMP_CRITICAL = 40.0    # °C — buzzer + alert
HUM_LOW       = 20.0    # %  — buzzer

# Timing
DHT_SAMPLE_INTERVAL = 10        # sec between DHT readings
AGGREGATION_WINDOW  = 6 * 60   # 6-minute aggregation window
DHT_MAX_RETRIES     = 3        # retries on DHT read error

# Digital sensor debounce
DEBOUNCE_LEN   = 5      # consecutive readings required for confirmation
CHECK_INTERVAL = 0.1    # sec between digital readings (10 Hz)

# ─────────────────────────────────────────────
# HARDWARE INIT
# ─────────────────────────────────────────────
print("Initializing hardware...")

dht_sensor = adafruit_dht.DHT11(DHT_PIN, use_pulseio=False)

rain   = Button(RAIN_PIN, pull_up=True)
fire   = Button(FIRE_PIN, pull_up=True)
gas    = Button(GAS_PIN,  pull_up=True)   # MQ-6
soil   = Button(SOIL_PIN, pull_up=True)
buzzer = Buzzer(BUZZER_PIN)

print("  DHT11  OK — GPIO4")
print("  Rain   OK — GPIO17")
print("  Fire   OK — GPIO27")
print("  Gas    OK — GPIO22 (MQ-6)")
print("  Soil   OK — GPIO23")
print("  Buzzer OK — GPIO18")

# ─────────────────────────────────────────────
# DATABASE — per-thread connection
# ─────────────────────────────────────────────
_local = threading.local()

def db_connect():
    """Returns a per-thread connection; reconnects if it has dropped."""
    conn = getattr(_local, "conn", None)
    if conn is None or conn.closed:
        try:
            conn = psycopg2.connect(
                dbname=DB_NAME,
                user=DB_USER,
                password=DB_PASS,
                host=DB_HOST
            )
            _local.conn = conn
            print(f"[DB] Connected ({threading.current_thread().name})")
        except Exception as e:
            print(f"[DB] Connection error: {e}")
            _local.conn = None
            return None
    return conn

def save_reading(sensor_type, value):
    """Saves a reading to the DB with automatic retry."""
    for attempt in range(3):
        try:
            conn = db_connect()
            if conn is None:
                raise Exception("No connection")
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO sensor_readings "
                "(sensor_type, value, timestamp) VALUES (%s, %s, %s)",
                (sensor_type, float(value), datetime.now())
            )
            conn.commit()
            cur.close()
            print(f"[DB] {sensor_type} = {value:.2f}")
            return
        except Exception as e:
            print(f"[DB] Write error (attempt {attempt+1}): {e}")
            _local.conn = None
            time.sleep(1)
    print(f"[DB] TOTAL FAILURE for {sensor_type} = {value}")

# ─────────────────────────────────────────────
# BUZZER — helper functions
# ─────────────────────────────────────────────
def buzzer_on_for(sec):
    """Turns the buzzer on for `sec` seconds (non-blocking via thread)."""
    buzzer.on()
    time.sleep(sec)
    buzzer.off()

def play_sos():
    """SOS morse pattern: ... --- ..."""
    for _ in range(3):
        buzzer.beep(0.1, 0.1, n=3)   # ...
        time.sleep(0.3)
        buzzer.beep(0.3, 0.1, n=3)   # ---
        time.sleep(0.3)
        buzzer.beep(0.1, 0.1, n=3)   # ...
        time.sleep(0.8)

def alert_buzzer(event_type):
    """Triggers a sound pattern specific to each event."""
    patterns = {
        "fire": lambda: buzzer_on_for(3),
        "gas":  play_sos,
        "rain": lambda: buzzer.beep(0.15, 0.15, n=3),
        "temp": lambda: buzzer_on_for(2),
        "hum":  lambda: buzzer.beep(0.1,  0.1,  n=3),
    }
    fn = patterns.get(event_type)
    if fn:
        threading.Thread(target=fn, daemon=True).start()

# ─────────────────────────────────────────────
# DIGITAL SENSORS — debounce + monitoring
# ─────────────────────────────────────────────
sensor_history = {
    "rain": collections.deque(maxlen=DEBOUNCE_LEN),
    "fire": collections.deque(maxlen=DEBOUNCE_LEN),
    "gas":  collections.deque(maxlen=DEBOUNCE_LEN),
    "soil": collections.deque(maxlen=DEBOUNCE_LEN),
}
sensor_state = dict.fromkeys(sensor_history, 0)

SENSOR_PINS = [
    ("rain", rain),
    ("fire", fire),
    ("gas",  gas),
    ("soil", soil),
]

def digital_monitor_loop():
    """
    Runs at 10 Hz.
    Confirms a transition only after DEBOUNCE_LEN consecutive identical readings.
    Saves to the DB only on state change (0->1 or 1->0).
    """
    print("[Digital] Thread started.")
    while True:
        for name, btn in SENSOR_PINS:
            val = 1 if btn.is_pressed else 0
            sensor_history[name].append(val)

            # 0 -> 1 transition (event detected)
            if all(sensor_history[name]) and sensor_state[name] == 0:
                sensor_state[name] = 1
                save_reading(name, 1)
                print(f"[ALERT] {name.upper()} DETECTED!")
                alert_buzzer(name)

            # 1 -> 0 transition (event ended)
            elif not any(sensor_history[name]) and sensor_state[name] == 1:
                sensor_state[name] = 0
                save_reading(name, 0)
                print(f"[INFO]  {name.upper()} ended.")

        time.sleep(CHECK_INTERVAL)

# ─────────────────────────────────────────────
# DHT11 — reading with retry + 6-minute aggregation
# ─────────────────────────────────────────────
dht_buffer = {
    "temperature": [],
    "humidity":    [],
}
dht_window_start = None

def read_dht11():
    """
    Reads DHT11 with up to DHT_MAX_RETRIES attempts.
    DHT11 has occasional errors — normal, retries absorb them.
    Returns (temp, hum) or (None, None) on total failure.
    """
    for attempt in range(DHT_MAX_RETRIES):
        try:
            temp = dht_sensor.temperature
            hum  = dht_sensor.humidity

            if temp is None or hum is None:
                raise ValueError("Null reading")
            if not (0 <= temp <= 60):
                raise ValueError(f"Temp out of range: {temp}")
            if not (0 <= hum <= 100):
                raise ValueError(f"Hum out of range: {hum}")

            return float(temp), float(hum)

        except Exception as e:
            print(f"[DHT11] Error (attempt {attempt+1}/{DHT_MAX_RETRIES}): {e}")
            if attempt < DHT_MAX_RETRIES - 1:
                time.sleep(2)   # DHT11 needs a pause between readings

    return None, None

def dht_loop():
    """
    Samples DHT11 every DHT_SAMPLE_INTERVAL seconds.
    At the end of each AGGREGATION_WINDOW window,
    calculates the average and saves it to the DB.
    """
    global dht_window_start
    dht_window_start = time.time()
    print("[DHT11] Thread started.")

    while True:
        temp, hum = read_dht11()

        if temp is not None:
            print(f"[DHT11] {temp:.1f}°C | {hum:.1f}%")
            dht_buffer["temperature"].append(temp)
            dht_buffer["humidity"].append(hum)
        else:
            print("[DHT11] Sample skipped (all retries failed).")

        now = time.time()
        elapsed = now - dht_window_start

        if elapsed >= AGGREGATION_WINDOW:
            n_samples = len(dht_buffer["temperature"])

            if n_samples > 0:
                avg_temp = sum(dht_buffer["temperature"]) / n_samples
                avg_hum  = sum(dht_buffer["humidity"])    / n_samples

                print(
                    f"[AVG 6min] Temp={avg_temp:.2f}°C | "
                    f"Hum={avg_hum:.2f}% | "
                    f"samples={n_samples}"
                )

                save_reading("temperature_avg_6min", avg_temp)
                save_reading("humidity_avg_6min",    avg_hum)

                # Alerts based on the aggregated average
                if avg_temp >= TEMP_CRITICAL:
                    print(f"[ALERT] Critical temperature: {avg_temp:.1f}°C!")
                    alert_buzzer("temp")

                if avg_hum <= HUM_LOW:
                    print(f"[ALERT] Low humidity: {avg_hum:.1f}%!")
                    alert_buzzer("hum")

            else:
                print("[AVG 6min] No valid reading in this window — skipping.")

            # Reset the window
            dht_buffer["temperature"].clear()
            dht_buffer["humidity"].clear()
            dht_window_start = now

        time.sleep(DHT_SAMPLE_INTERVAL)

# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
if __name__ == "__main__":
    print("\n=== Greenhouse Monitor — production version")
    print(f"   DB:                {DB_HOST}/{DB_NAME}")
    print(f"   Aggregation:       {AGGREGATION_WINDOW // 60} minutes")
    print(f"   DHT sample rate:   every {DHT_SAMPLE_INTERVAL} sec")
    print(f"   Debounce:          {DEBOUNCE_LEN} readings x {CHECK_INTERVAL}s")
    print(f"   Critical temp:     {TEMP_CRITICAL}°C")
    print(f"   Minimum humidity:  {HUM_LOW}%\n")

    try:
        # Check the DB connection on startup
        if db_connect() is None:
            print("ERROR: Cannot connect to DB. Check .env and PostgreSQL.")
            exit(1)

        print("DB OK. Starting threads...\n")

        # Startup confirmation beep
        threading.Thread(
            target=lambda: buzzer.beep(0.05, 0.05, n=3),
            daemon=True
        ).start()

        # Digital sensors thread
        t_digital = threading.Thread(
            target=digital_monitor_loop,
            name="digital",
            daemon=True
        )
        t_digital.start()

        # DHT11 + aggregation thread
        t_dht = threading.Thread(
            target=dht_loop,
            name="dht",
            daemon=True
        )
        t_dht.start()

        print("All threads started. Ctrl+C to stop.\n")

        # Main thread stays alive
        while True:
            time.sleep(1)

    except KeyboardInterrupt:
        print("\nManual shutdown.")

    finally:
        # Clean up resources on exit
        try:
            dht_sensor.exit()
            print("DHT11 released.")
        except:
            pass

        try:
            buzzer.off()
        except:
            pass

        try:
            conn = getattr(_local, "conn", None)
            if conn and not conn.closed:
                conn.close()
                print("DB connection closed.")
        except:
            pass

        print("Greenhouse Monitor stopped cleanly.")
