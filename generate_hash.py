#!/usr/bin/env python3
"""
Generates the dashboard password hash.
Run:  python3 generate_hash.py
Copy the result into .env as GH_PASS_HASH=...
"""
import getpass
from werkzeug.security import generate_password_hash

pwd = getpass.getpass("New dashboard password: ")
pwd2 = getpass.getpass("Repeat password: ")

if pwd != pwd2:
    print("Passwords do not match. Try again.")
    raise SystemExit(1)
if len(pwd) < 8:
    print("Choose a password with at least 8 characters.")
    raise SystemExit(1)

print("\nAdd the line below to your .env file:\n")
print(f"GH_PASS_HASH={generate_password_hash(pwd)}")
