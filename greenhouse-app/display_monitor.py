#!/usr/bin/env python3
"""
Greenhouse Display Monitor - Pi5
TFT 1.8" ST7735S 160x128 — modul 11 pini

Pinout modul -> Raspberry Pi 5 (CONFIRMAT FUNCTIONAL):
  Pin  1 VCC -> 3.3V       (pin fizic 17)
  Pin  2 GND -> GND        (pin fizic 6)
  Pin  3 GND -> GND        (pin fizic 14)
  Pin  4 NC  -> neconectat
  Pin  5 NC  -> neconectat
  Pin  6 NC  -> neconectat
  Pin  7 CLK -> GPIO11     (pin fizic 23, SPI SCLK)
  Pin  8 SDA -> GPIO10     (pin fizic 19, SPI MOSI)
  Pin  9 RS  -> GPIO5      (pin fizic 29, CS)
  Pin 10 RST -> GPIO25     (pin fizic 22, DC)
  Pin 11 CS  -> GPIO24     (pin fizic 18, RST)

Pagina 1 (5 sec): Temperatura & Umiditate
Pagina 2 (5 sec): Status senzori digitali (rain/fire/gas/soil)

Dependente:
  pip install adafruit-circuitpython-rgb-display pillow psycopg2-binary
"""

import time
import threading
import os
import board
import digitalio
import psycopg2

from PIL import Image, ImageDraw, ImageFont
from adafruit_rgb_display import st7735

# ─────────────────────────────────────────────
# CONFIG — exclusiv din .env, fara parole in cod
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

DISPLAY_WIDTH  = 128
DISPLAY_HEIGHT = 160
PAGE_INTERVAL  = 5      # secunde per pagina
DB_POLL        = 10     # secunde intre citiri DB

# ─────────────────────────────────────────────
# CULORI (RGB)
# ─────────────────────────────────────────────
C_BG        = (10,  13,  15)    # fundal negru
C_CARD      = (18,  24,  32)    # card dark
C_GREEN     = (34,  197, 94)    # verde accent
C_RED       = (239, 68,  68)    # rosu alerta
C_AMBER     = (245, 158, 11)    # amber
C_BLUE      = (56,  189, 248)   # albastru
C_MUTED     = (77,  96,  112)   # text secundar
C_WHITE     = (232, 237, 242)   # text principal
C_DIM       = (30,  42,  54)    # border/separator

# ─────────────────────────────────────────────
# INIT DISPLAY SPI
# ─────────────────────────────────────────────
# Pinout confirmat functional:
# CS  -> GPIO5  (pin fizic 29)
# DC  -> GPIO25 (pin fizic 22)
# RST -> GPIO24 (pin fizic 18)
# CLK -> GPIO11 (pin fizic 23, auto SPI)
# SDA -> GPIO10 (pin fizic 19, auto SPI)
spi = board.SPI()

cs  = digitalio.DigitalInOut(board.D5)    # GPIO5  - pin 29
dc  = digitalio.DigitalInOut(board.D25)   # GPIO25 - pin 22
rst = digitalio.DigitalInOut(board.D24)   # GPIO24 - pin 18

display = st7735.ST7735R(
    spi,
    cs=cs, dc=dc, rst=rst,
    width=DISPLAY_WIDTH, height=DISPLAY_HEIGHT,
    rotation=90,
    baudrate=24_000_000,
)

# ─────────────────────────────────────────────
# FONTURI
# ─────────────────────────────────────────────
# Pillow vine cu DejaVu embedded; cauta si fonts system
def load_font(size):
    paths = [
        f"/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
        f"/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        f"/usr/share/fonts/truetype/liberation/LiberationMono-Bold.ttf",
    ]
    for p in paths:
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()

FONT_HUGE   = load_font(38)   # valori mari temp/hum
FONT_LARGE  = load_font(18)   # titluri pagina
FONT_MEDIUM = load_font(13)   # labels, valori senzori
FONT_SMALL  = load_font(10)   # subtitluri, unitati

# ─────────────────────────────────────────────
# DB — citire date curente
# ─────────────────────────────────────────────
_local = threading.local()

def db_connect():
    conn = getattr(_local, "conn", None)
    if conn is None or conn.closed:
        try:
            conn = psycopg2.connect(
                dbname=DB_NAME, user=DB_USER,
                password=DB_PASS, host=DB_HOST
            )
            _local.conn = conn
        except Exception as e:
            print("DB connect error:", e)
            _local.conn = None
            return None
    return conn

def last_value(sensor):
    """Returneaza ultima valoare a unui senzor din DB."""
    try:
        conn = db_connect()
        if not conn:
            return None
        cur = conn.cursor()
        cur.execute("""
            SELECT value FROM sensor_readings
            WHERE sensor_type = %s
            ORDER BY timestamp DESC LIMIT 1
        """, (sensor,))
        row = cur.fetchone()
        cur.close()
        return float(row[0]) if row else None
    except Exception as e:
        print("DB read error:", e)
        _local.conn = None
        return None

# ─────────────────────────────────────────────
# STATE — date partajate intre thread-uri
# ─────────────────────────────────────────────
state = {
    "temp":  None,
    "hum":   None,
    "rain":  None,
    "fire":  None,
    "gas":   None,
    "soil":  None,
    "last_update": None,
}
state_lock = threading.Lock()

def poll_db():
    """Thread: citeste DB la fiecare DB_POLL secunde."""
    while True:
        data = {
            "temp":  last_value("temperature_avg_6min"),
            "hum":   last_value("humidity_avg_6min"),
            "rain":  last_value("rain"),
            "fire":  last_value("fire"),
            "gas":   last_value("gas"),
            "soil":  last_value("soil"),
            "last_update": time.strftime("%H:%M"),
        }
        with state_lock:
            state.update(data)
        print(f"[Display] Temp={data['temp']} Hum={data['hum']} "
              f"Rain={data['rain']} Fire={data['fire']}")
        time.sleep(DB_POLL)

# ─────────────────────────────────────────────
# DESENARE
# ─────────────────────────────────────────────
def new_canvas():
    img  = Image.new("RGB", (DISPLAY_HEIGHT, DISPLAY_WIDTH), C_BG)
    draw = ImageDraw.Draw(img)
    return img, draw

def draw_rect(draw, x, y, w, h, color, radius=6):
    """Dreptunghi cu colturi rotunjite (simulat cu ellipse + rect)."""
    draw.rounded_rectangle([x, y, x+w, y+h], radius=radius, fill=color)

def draw_topbar(draw, title, page_num, total_pages):
    """Bara superioara cu titlu si indicatoare pagina."""
    draw.rectangle([0, 0, DISPLAY_WIDTH, 20], fill=C_CARD)
    draw.text((6, 3), title, font=FONT_SMALL, fill=C_GREEN)

    # puncte indicator pagina (dreapta)
    dot_x = DISPLAY_WIDTH - 6
    for i in range(total_pages - 1, -1, -1):
        color = C_GREEN if i == page_num else C_DIM
        draw.ellipse([dot_x-5, 7, dot_x, 12], fill=color)
        dot_x -= 9

def draw_bottombar(draw, last_update):
    """Bara inferioara cu ora ultimei actualizari."""
    draw.rectangle([0, DISPLAY_HEIGHT-14, DISPLAY_WIDTH, DISPLAY_HEIGHT], fill=C_CARD)
    txt = f"upd {last_update}" if last_update else "no data"
    draw.text((6, DISPLAY_HEIGHT-12), txt, font=FONT_SMALL, fill=C_MUTED)

def temp_color(val):
    if val is None: return C_MUTED
    if val < 15:    return C_BLUE
    if val < 25:    return C_GREEN
    if val < 32:    return C_AMBER
    return C_RED

def hum_color(val):
    if val is None: return C_MUTED
    if val < 30:    return C_RED
    if val < 55:    return C_AMBER
    if val < 75:    return C_GREEN
    return C_BLUE

# ─────────────────────────────────────────────
# PAGINA 1: Temperatura & Umiditate
# ─────────────────────────────────────────────
def render_page_climate(s):
    img, draw = new_canvas()

    draw_topbar(draw, "CLIMAT", 0, 2)
    draw_bottombar(draw, s["last_update"])

    temp = s["temp"]
    hum  = s["hum"]

    # ── Temperatura (stanga) ──
    draw_rect(draw, 2, 24, 76, 72, C_CARD, radius=8)
    draw.text((8, 27), "TEMP", font=FONT_SMALL, fill=C_MUTED)

    if temp is not None:
        t_str = f"{temp:.1f}"
        draw.text((8, 40), t_str, font=FONT_HUGE, fill=temp_color(temp))
        draw.text((60, 42), "\u00b0C", font=FONT_SMALL, fill=C_MUTED)
    else:
        draw.text((8, 48), "N/A", font=FONT_LARGE, fill=C_MUTED)

    # bara progres temperatura (0-50°C)
    bar_w = 68
    bar_h = 5
    bar_x, bar_y = 6, 90
    draw.rectangle([bar_x, bar_y, bar_x+bar_w, bar_y+bar_h], fill=C_DIM)
    if temp is not None:
        fill_w = int(bar_w * min(max(temp, 0), 50) / 50)
        if fill_w > 0:
            draw.rectangle([bar_x, bar_y, bar_x+fill_w, bar_y+bar_h], fill=temp_color(temp))

    # zona optima indicator
    opt_start = int(bar_w * 15 / 50)
    opt_end   = int(bar_w * 30 / 50)
    draw.rectangle([bar_x+opt_start, bar_y-2, bar_x+opt_end, bar_y+bar_h+2],
                   outline=C_GREEN, width=1)

    # ── Umiditate (dreapta) ──
    draw_rect(draw, 82, 24, 76, 72, C_CARD, radius=8)
    draw.text((88, 27), "HUM", font=FONT_SMALL, fill=C_MUTED)

    if hum is not None:
        h_str = f"{hum:.1f}"
        draw.text((88, 40), h_str, font=FONT_HUGE, fill=hum_color(hum))
        draw.text((138, 42), "%", font=FONT_SMALL, fill=C_MUTED)
    else:
        draw.text((88, 48), "N/A", font=FONT_LARGE, fill=C_MUTED)

    # bara progres umiditate
    bar_x2 = 86
    draw.rectangle([bar_x2, bar_y, bar_x2+bar_w, bar_y+bar_h], fill=C_DIM)
    if hum is not None:
        fill_w2 = int(bar_w * min(max(hum, 0), 100) / 100)
        if fill_w2 > 0:
            draw.rectangle([bar_x2, bar_y, bar_x2+fill_w2, bar_y+bar_h], fill=hum_color(hum))

    opt_start2 = int(bar_w * 40 / 100)
    opt_end2   = int(bar_w * 75 / 100)
    draw.rectangle([bar_x2+opt_start2, bar_y-2, bar_x2+opt_end2, bar_y+bar_h+2],
                   outline=C_GREEN, width=1)

    # ── Status text jos ──
    y_status = 100
    if temp is not None and hum is not None:
        if temp >= 40 or hum <= 20:
            msg, col = "! ATENTIE !", C_RED
        elif 20 <= temp <= 30 and 40 <= hum <= 75:
            msg, col = "\u2713 Conditii OK", C_GREEN
        else:
            msg, col = "~ Suboptimal", C_AMBER
    else:
        msg, col = "Astept date...", C_MUTED

    draw.text((6, y_status), msg, font=FONT_SMALL, fill=col)

    return img

# ─────────────────────────────────────────────
# PAGINA 2: Senzori digitali
# ─────────────────────────────────────────────
SENSOR_DEFS = [
    ("rain", "PLOAIE"),
    ("fire", "FOC"),
    ("gas",  "GAZ"),
    ("soil", "SOL"),
]

def render_page_sensors(s):
    img, draw = new_canvas()

    draw_topbar(draw, "SENZORI", 1, 2)
    draw_bottombar(draw, s["last_update"])

    # 4 carduri 2x2
    positions = [
        (2,  22),   # rain  — stanga sus
        (82, 22),   # fire  — dreapta sus
        (2,  74),   # gas   — stanga jos
        (82, 74),   # soil  — dreapta jos
    ]

    for i, (key, label) in enumerate(SENSOR_DEFS):
        x, y   = positions[i]
        val    = s[key]
        active = (val == 1.0)

        bg_color  = (40, 10, 10)  if active else C_CARD
        brd_color = C_RED         if active else C_DIM
        val_color = C_RED         if active else C_GREEN
        val_text  = "ACTIV"       if active else "OK"

        # card background
        draw_rect(draw, x, y, 76, 48, bg_color, radius=8)

        # border colorat la alerta
        if active:
            draw.rounded_rectangle([x, y, x+76, y+48], radius=8,
                                   outline=brd_color, width=2)

        # label
        draw.text((x+6, y+6), label, font=FONT_SMALL, fill=C_MUTED)

        # valoare
        draw.text((x+6, y+26), val_text, font=FONT_MEDIUM, fill=val_color)

        # dot indicator
        dot_col = C_RED if active else C_GREEN
        draw.ellipse([x+60, y+6, x+70, y+16], fill=dot_col)
        if active:
            # glow effect simulat cu ellipse mai mare semitransparent
            draw.ellipse([x+57, y+3, x+73, y+19], outline=C_RED, width=1)

        # daca nu avem date
        if val is None:
            draw.text((x+6, y+26), "N/A", font=FONT_MEDIUM, fill=C_MUTED)

    return img

# ─────────────────────────────────────────────
# LOOP PRINCIPAL DISPLAY
# ─────────────────────────────────────────────
PAGES = [render_page_climate, render_page_sensors]

def display_loop():
    page = 0
    while True:
        with state_lock:
            s = dict(state)   # snapshot

        try:
            img = PAGES[page](s)
            display.image(img)
        except Exception as e:
            print(f"[Display] Render error: {e}")

        time.sleep(PAGE_INTERVAL)
        page = (page + 1) % len(PAGES)

# ─────────────────────────────────────────────
# ECRAN DE BOOT (splash)
# ─────────────────────────────────────────────
def splash_screen():
    img, draw = new_canvas()
    draw.text((20, 40), "Green", font=FONT_LARGE, fill=C_WHITE)
    draw.text((20, 60), "House", font=FONT_LARGE, fill=C_GREEN)
    draw.text((20, 84), "Monitor v1.0", font=FONT_SMALL, fill=C_MUTED)
    draw.line([(18, 80), (142, 80)], fill=C_DIM, width=1)

    # loading bar animata
    for i in range(1, 11):
        img2 = img.copy()
        d2   = ImageDraw.Draw(img2)
        bar_w = int(124 * i / 10)
        d2.rectangle([18, 100, 18+bar_w, 108], fill=C_GREEN)
        d2.rectangle([18, 100, 142, 108], outline=C_DIM, width=1)
        display.image(img2)
        time.sleep(0.12)

    time.sleep(0.5)

# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
if __name__ == "__main__":
    print("=== Greenhouse Display Monitor pornit ===")
    print(f"   Display: ST7735 {DISPLAY_WIDTH}x{DISPLAY_HEIGHT}")
    print(f"   Pagini: {len(PAGES)} x {PAGE_INTERVAL}s")
    print(f"   Poll DB: la {DB_POLL}s")

    try:
        splash_screen()

        # thread DB polling
        t_db = threading.Thread(target=poll_db, name="db-poll", daemon=True)
        t_db.start()

        # asteapta prima citire DB
        time.sleep(1.5)

        # loop display (blocant pe main thread)
        display_loop()

    except KeyboardInterrupt:
        print("\nOprit.")
    finally:
        # stinge display-ul la oprire
        try:
            img, draw = new_canvas()
            display.image(img)
        except:
            pass
        print("Display oprit.")
