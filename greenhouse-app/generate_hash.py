#!/usr/bin/env python3
"""
Genereaza hash-ul parolei pentru dashboard.
Ruleaza:  python3 generate_hash.py
Copiaza rezultatul in .env la GH_PASS_HASH=...
"""
import getpass
from werkzeug.security import generate_password_hash

pwd = getpass.getpass("Parola noua pentru dashboard: ")
pwd2 = getpass.getpass("Repeta parola: ")

if pwd != pwd2:
    print("Parolele nu coincid. Incearca din nou.")
    raise SystemExit(1)
if len(pwd) < 8:
    print("Alege o parola de minim 8 caractere.")
    raise SystemExit(1)

print("\nAdauga linia de mai jos in fisierul .env:\n")
print(f"GH_PASS_HASH={generate_password_hash(pwd)}")
