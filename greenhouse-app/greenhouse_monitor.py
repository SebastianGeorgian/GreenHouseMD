#!/usr/bin/env python3
"""
Greenhouse Monitor — versiune finala
Raspberry Pi 5 + toti senzori reali

Senzori:
  DHT11        — temperatura + umiditate    GPIO4  (pin 7)
  Ploaie       — detectare precipitatii     GPIO17 (pin 11)
  Buzzer       — alerta sonora              GPIO18 (pin 12)
  Foc (IR)     — detectare flacara          GPIO27 (pin 13)
  Gaz MQ-6     — LPG / butan / propan       GPIO22 (pin 15)
  Sol          — umiditate sol              GPIO23 (pin 16)

Dependente:
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
# CONFIGURARE — exclusiv din .env, fara parole in cod
# ─────────────────────────────────────────────
from dotenv import load_dotenv
load_dotenv()

def require_env(name):
    val = os.getenv(name)
    if not val:
        print(f"EROARE: variabila '{name}' lipseste din .env. "
              f"Copiaza .env.example in .env si completeaz-o.")
        raise SystemExit(1)
    return val

DB_NAME = os.getenv("DB_NAME", "greenhouse")
DB_USER = os.getenv("DB_USER", "pi_sebastian")
DB_PASS = require_env("DB_PASSWORD")
DB_HOST = os.getenv("DB_HOST", "localhost")

# Pini BCM
DHT_PIN    = board.D4   # GPIO4
RAIN_PIN   = 17         # GPIO17
FIRE_PIN   = 27         # GPIO27
GAS_PIN    = 22         # GPIO22 — MQ-6
SOIL_PIN   = 23         # GPIO23
BUZZER_PIN = 18         # GPIO18

# Praguri alerta
TEMP_CRITICAL = 40.0    # °C — buzzer + alerta
HUM_LOW       = 20.0    # %  — buzzer

# Timpi
DHT_SAMPLE_INTERVAL = 10        # sec intre citiri DHT
AGGREGATION_WINDOW  = 6 * 60   # 6 minute fereastra agregare
DHT_MAX_RETRIES     = 3        # retry-uri la eroare DHT

# Debounce senzori digitali
DEBOUNCE_LEN   = 5      # citiri consecutive pentru confirmare
CHECK_INTERVAL = 0.1    # sec intre citiri digitale (10 Hz)

# ─────────────────────────────────────────────
# INIT HARDWARE
# ─────────────────────────────────────────────
print("Initializez hardware...")

dht_sensor = adafruit_dht.DHT11(DHT_PIN, use_pulseio=False)

rain   = Button(RAIN_PIN, pull_up=True)
fire   = Button(FIRE_PIN, pull_up=True)
gas    = Button(GAS_PIN,  pull_up=True)   # MQ-6
soil   = Button(SOIL_PIN, pull_up=True)
buzzer = Buzzer(BUZZER_PIN)

print("  DHT11  OK — GPIO4")
print("  Ploaie OK — GPIO17")
print("  Foc    OK — GPIO27")
print("  Gaz    OK — GPIO22 (MQ-6)")
print("  Sol    OK — GPIO23")
print("  Buzzer OK — GPIO18")

# ─────────────────────────────────────────────
# BAZA DE DATE — conexiune per-thread
# ─────────────────────────────────────────────
_local = threading.local()

def db_connect():
    """Returneaza conexiune per-thread; reconecteaza daca e cazuta."""
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
            print(f"[DB] Conectat ({threading.current_thread().name})")
        except Exception as e:
            print(f"[DB] Eroare conectare: {e}")
            _local.conn = None
            return None
    return conn

def save_reading(sensor_type, value):
    """Salveaza o citire in DB cu retry automat."""
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
            print(f"[DB] Eroare scriere (attempt {attempt+1}): {e}")
            _local.conn = None
            time.sleep(1)
    print(f"[DB] ESEC TOTAL pentru {sensor_type} = {value}")

# ─────────────────────────────────────────────
# BUZZER — functii helper
# ─────────────────────────────────────────────
def buzzer_on_for(sec):
    """Porneste buzzer pentru `sec` secunde (non-blocking prin thread)."""
    buzzer.on()
    time.sleep(sec)
    buzzer.off()

def play_sos():
    """Secventa SOS morse: ... --- ..."""
    for _ in range(3):
        buzzer.beep(0.1, 0.1, n=3)   # ...
        time.sleep(0.3)
        buzzer.beep(0.3, 0.1, n=3)   # ---
        time.sleep(0.3)
        buzzer.beep(0.1, 0.1, n=3)   # ...
        time.sleep(0.8)

def alert_buzzer(event_type):
    """Declanseaza pattern sonor specific fiecarui eveniment."""
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
# SENZORI DIGITALI — debounce + monitorizare
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
    Ruleaza la 10 Hz.
    Confirma tranzitie doar dupa DEBOUNCE_LEN citiri consecutive identice.
    Salveaza in DB doar la schimbarea de stare (0->1 sau 1->0).
    """
    print("[Digital] Thread pornit.")
    while True:
        for name, btn in SENSOR_PINS:
            val = 1 if btn.is_pressed else 0
            sensor_history[name].append(val)

            # tranzitie 0 -> 1 (eveniment detectat)
            if all(sensor_history[name]) and sensor_state[name] == 0:
                sensor_state[name] = 1
                save_reading(name, 1)
                print(f"[ALERT] {name.upper()} DETECTAT!")
                alert_buzzer(name)

            # tranzitie 1 -> 0 (eveniment incheiat)
            elif not any(sensor_history[name]) and sensor_state[name] == 1:
                sensor_state[name] = 0
                save_reading(name, 0)
                print(f"[INFO]  {name.upper()} incheiat.")

        time.sleep(CHECK_INTERVAL)

# ─────────────────────────────────────────────
# DHT11 — citire cu retry + agregare 6 minute
# ─────────────────────────────────────────────
dht_buffer = {
    "temperature": [],
    "humidity":    [],
}
dht_window_start = None

def read_dht11():
    """
    Citeste DHT11 cu pana la DHT_MAX_RETRIES incercari.
    DHT11 are erori ocazionale — normal, retry-urile le absorb.
    Returneaza (temp, hum) sau (None, None) la esec total.
    """
    for attempt in range(DHT_MAX_RETRIES):
        try:
            temp = dht_sensor.temperature
            hum  = dht_sensor.humidity

            if temp is None or hum is None:
                raise ValueError("Citire nula")
            if not (0 <= temp <= 60):
                raise ValueError(f"Temp in afara range: {temp}")
            if not (0 <= hum <= 100):
                raise ValueError(f"Hum in afara range: {hum}")

            return float(temp), float(hum)

        except Exception as e:
            print(f"[DHT11] Eroare (attempt {attempt+1}/{DHT_MAX_RETRIES}): {e}")
            if attempt < DHT_MAX_RETRIES - 1:
                time.sleep(2)   # DHT11 are nevoie de pauza intre citiri

    return None, None

def dht_loop():
    """
    Eșantionează DHT11 la fiecare DHT_SAMPLE_INTERVAL secunde.
    La sfarsitul fiecarei ferestre de AGGREGATION_WINDOW secunde,
    calculeaza media si o salveaza in DB.
    """
    global dht_window_start
    dht_window_start = time.time()
    print("[DHT11] Thread pornit.")

    while True:
        temp, hum = read_dht11()

        if temp is not None:
            print(f"[DHT11] {temp:.1f}°C | {hum:.1f}%")
            dht_buffer["temperature"].append(temp)
            dht_buffer["humidity"].append(hum)
        else:
            print("[DHT11] Sample sarit (toate retry-urile au esuat).")

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

                # Alerte pe baza mediei agregate
                if avg_temp >= TEMP_CRITICAL:
                    print(f"[ALERT] Temperatura critica: {avg_temp:.1f}°C!")
                    alert_buzzer("temp")

                if avg_hum <= HUM_LOW:
                    print(f"[ALERT] Umiditate scazuta: {avg_hum:.1f}%!")
                    alert_buzzer("hum")

            else:
                print("[AVG 6min] Nicio citire valida in fereastra — skip.")

            # Reseteaza fereastra
            dht_buffer["temperature"].clear()
            dht_buffer["humidity"].clear()
            dht_window_start = now

        time.sleep(DHT_SAMPLE_INTERVAL)

# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
if __name__ == "__main__":
    print("\n=== Greenhouse Monitor — versiune finala")
    print(f"   DB:           {DB_HOST}/{DB_NAME}")
    print(f"   Agregare:     {AGGREGATION_WINDOW // 60} minute")
    print(f"   Sample DHT:   la {DHT_SAMPLE_INTERVAL} sec")
    print(f"   Debounce:     {DEBOUNCE_LEN} citiri x {CHECK_INTERVAL}s")
    print(f"   Temp critica: {TEMP_CRITICAL}°C")
    print(f"   Hum minima:   {HUM_LOW}%\n")

    try:
        # Verifica conexiunea DB la pornire
        if db_connect() is None:
            print("EROARE: Nu pot conecta la DB. Verifica .env si PostgreSQL.")
            exit(1)

        print("DB OK. Pornesc thread-urile...\n")

        # Bip de confirmare pornire
        threading.Thread(
            target=lambda: buzzer.beep(0.05, 0.05, n=3),
            daemon=True
        ).start()

        # Thread senzori digitali
        t_digital = threading.Thread(
            target=digital_monitor_loop,
            name="digital",
            daemon=True
        )
        t_digital.start()

        # Thread DHT11 + agregare
        t_dht = threading.Thread(
            target=dht_loop,
            name="dht",
            daemon=True
        )
        t_dht.start()

        print("Toate thread-urile pornite. Ctrl+C pentru oprire.\n")

        # Main thread ramane activ
        while True:
            time.sleep(1)

    except KeyboardInterrupt:
        print("\nOprire manuala.")

    finally:
        # Curata resurse la oprire
        try:
            dht_sensor.exit()
            print("DHT11 eliberat.")
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
                print("DB inchis.")
        except:
            pass

        print("Greenhouse Monitor oprit curat.")
