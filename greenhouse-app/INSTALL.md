# Greenhouse Monitor — instalare și configurare

Pașii de mai jos pornesc de la fișierele din acest pachet, copiate în
`/home/greenhouse/Desktop/GreenHouse` pe Raspberry Pi. Dacă folosești alt
director sau alt utilizator, ajustează căile din `deploy/*.service` și din
`start_greenhouse.sh`.

## 1. Dependențe

```bash
cd /home/greenhouse/Desktop/GreenHouse
pip install -r requirements.txt --break-system-packages
```

## 2. Baza de date

Creează indexul de performanță (tabela există deja la tine; comanda e
idempotentă, poate fi rulată oricând):

```bash
psql -U pi_sebastian -d greenhouse -f schema.sql
```

## 3. Configurare `.env`

```bash
cp .env.example .env
nano .env
```

Completează obligatoriu:

- `DB_PASSWORD` — parola PostgreSQL (cea actuală; ideal, schimb-o pe una nouă)
- `FLASK_SECRET` — generează cu `python3 -c "import secrets; print(secrets.token_hex(32))"`
- `GH_PASS_HASH` — generează cu `python3 generate_hash.py` (îți cere parola nouă de dashboard și afișează hash-ul)

Important: aplicația **refuză să pornească** fără aceste trei valori — nu mai
există parole implicite în cod. Adaugă `.env` în `.gitignore` dacă urci
proiectul pe git.

## 4. Pornire ca servicii systemd (recomandat)

```bash
# acces GPIO/SPI fără root pentru utilizatorul greenhouse
sudo usermod -aG gpio,spi greenhouse

sudo cp deploy/*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now greenhouse-monitor greenhouse-display greenhouse-web
```

Verificare și loguri:

```bash
systemctl status greenhouse-web
journalctl -u greenhouse-monitor -f
```

Serviciile pornesc automat la boot și repornesc singure în maximum 5 secunde
dacă un script crapă — argumentul de fiabilitate pentru lucrare.

Pentru teste rapide fără systemd rămâne `./start_greenhouse.sh`.

## 5. Ce s-a schimbat în aplicație

**Securitate**
- credențialele vin exclusiv din `.env`; nicio parolă nu mai apare în cod
- parola de dashboard e stocată ca hash (werkzeug), nu în clar
- rate limiting pe `/login`: maxim 8 încercări / 5 minute / IP
- toate endpoint-urile de date cer autentificare (înainte `/api/timeseries`,
  `/api/heatmap` și `/api/history` erau publice)
- intervalul SQL din `timeseries()` e transmis ca parametru (`%s::interval`),
  nu interpolat în query

**Robustețe**
- server WSGI real (gunicorn, 1 worker × 4 thread-uri) în loc de serverul de
  dezvoltare Flask
- pool de conexiuni PostgreSQL (`ThreadedConnectionPool`) în loc de conexiune
  nouă la fiecare request
- index compus `(sensor_type, timestamp DESC)` pe tabela de citiri
- servicii systemd cu restart automat și loguri în journal

**Funcționalități noi**
- detectare senzor offline: dacă media de climă nu s-a mai scris de peste
  15 minute (configurabil, `CLIMATE_OFFLINE_SEC`), badge-ul „transmisie live"
  din topbar devine roșu și arată de câte minute lipsesc datele
- panou „Istoric alerte": evenimentele de foc/gaz din ultimele 90 de zile,
  reconstruite din tranzițiile 1→0, cu data de start și durata; alertele încă
  active apar evidențiate „în desfășurare"
- buton „Descarcă CSV": exportă temperatura și umiditatea din intervalul
  selectat în grafic (1 zi / 1 lună / 3 luni), util și pentru graficele din
  lucrare
- bandă „Afară vs. În seră": vremea locală de la Open-Meteo (fără cheie API,
  cache 10 minute pe server) lângă valorile curente din seră; coordonatele se
  setează în `.env` (`WEATHER_LAT`, `WEATHER_LON`)

## 6. Notă despre gunicorn și thread-ul de alerte

Thread-ul care verifică alertele (foc/gaz → buzzer + Telegram) pornește la
importul modulului, o singură dată. De aceea serviciul web rulează cu **un
singur worker** (`-w 1 --threads 4`); cu mai mulți workeri, alertele s-ar
trimite duplicat.
