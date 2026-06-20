#!/bin/bash
# ─────────────────────────────────────────────
# Pornire MANUALA (doar pentru dezvoltare/test).
# In productie foloseste serviciile systemd din deploy/
# (pornesc la boot si repornesc automat la eroare):
#
#   sudo cp deploy/*.service /etc/systemd/system/
#   sudo systemctl daemon-reload
#   sudo systemctl enable --now greenhouse-monitor greenhouse-display greenhouse-web
#
# Loguri:  journalctl -u greenhouse-web -f
# ─────────────────────────────────────────────

cd /home/greenhouse/Desktop/GreenHouse

/usr/bin/python3 display_monitor.py &
/usr/bin/python3 greenhouse_monitor.py &
/usr/bin/python3 -m gunicorn -w 1 --threads 4 -b 0.0.0.0:5000 app:app
