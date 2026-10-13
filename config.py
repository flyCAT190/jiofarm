"""Configuration loader for jiofarm.
Reads from environment variables or .env file (stdlib only).
"""
from __future__ import annotations
import os
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
ENV_PATH = ROOT_DIR / ".env"

if ENV_PATH.exists():
    for line in ENV_PATH.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip("\"'"))

TG_TOKEN = os.getenv("TG_TOKEN", "")
TG_CHAT = os.getenv("TG_CHAT", "")
TARGET_COUNT = int(os.getenv("TARGET_COUNT", "100"))
SEND_GAP = int(os.getenv("SEND_GAP", "45"))
OTP_WAIT = int(os.getenv("OTP_WAIT", "90"))
LOCK_COOLDOWN = int(os.getenv("LOCK_COOLDOWN", "900"))

_pf = os.getenv("PANELS_FILE", "panels.txt")
PANELS_FILE = Path(_pf) if Path(_pf).is_absolute() else (ROOT_DIR / _pf)
