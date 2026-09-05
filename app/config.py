"""App configuration, driven by environment variables with dev-friendly defaults."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# Temp/working database — holds only adoption-run state (jobs, per-switch
# results, and adoption codes fetched from Mist). It never holds credentials
# (Mist API token / AOS-CX password), which stay in memory for the life of a
# single run only. Safe to wipe between runs.
DB_PATH = os.getenv("ADOPT_DB_PATH", str(BASE_DIR / "adopt.db"))

# Subpath the app is served under behind a reverse proxy / the HPE edge
# (e.g. "/adopt"). Empty = root.
ROOT_PATH = os.getenv("ADOPT_ROOT_PATH", "")

# ── Mist defaults (all overridable per-run from the dashboard form) ─────────
DEFAULT_MIST_HOST = os.getenv("ADOPT_MIST_HOST", "api.mist.com")
# Test-only escape hatch: point the Mist client at a mock server
# (e.g. http://127.0.0.1:9100) instead of building https://{host}. Leave
# unset in production.
MIST_BASE_URL_OVERRIDE = os.getenv("ADOPT_MIST_BASE_URL_OVERRIDE", "") or None

# ── AOS-CX defaults ──────────────────────────────────────────────────────
# Must be a version pyaoscx actually ships REST bindings for — check
# `python -c "import pyaoscx.rest as r, pkgutil; print([m.name for m in pkgutil.iter_modules(r.__path__)])"`
# for the installed pyaoscx version. pyaoscx==2.6.0 (pinned in
# requirements.txt) supports 10.04, 10.08, and 10.09 — NOT 10.13 despite that
# being a real, newer AOS-CX firmware release.
DEFAULT_AOSCX_API_VERSION = os.getenv("ADOPT_AOSCX_API_VERSION", "10.09")
# NOTE: pyaoscx's Session.open() hardcodes TLS verification off (switches
# ship with a self-signed cert), so there is no verify-TLS toggle to wire up
# here — this is documentation of that behavior, not a live setting.
# Test-only escape hatch: talk plain http to a mock switch instead of https.
CX_SCHEME = os.getenv("ADOPT_CX_SCHEME", "https")

# Model-name prefixes treated as Juniper EX switches — Mist inventory can
# return EX (Juniper) and CX (Aruba) devices side by side under type=switch,
# and EX adoption codes are not valid for a CX switch. Adjust if your org
# sees other Juniper switch families (e.g. QFX) mixed into the same org.
EX_MODEL_PREFIXES = tuple(
    p.strip().upper()
    for p in os.getenv("ADOPT_EX_MODEL_PREFIXES", "EX,QFX").split(",")
    if p.strip()
)

# The exact AOS-CX `system.aruba_central` field that accepts the Mist/Central
# claim (adoption) code has not been confirmed against real switch firmware
# (see app/cx_client.py). Kept as a single override point.
MIST_CLAIM_FIELD = os.getenv("ADOPT_MIST_CLAIM_FIELD", "activation_key")

# How many worker threads push codes to switches concurrently.
PUSH_CONCURRENCY = int(os.getenv("ADOPT_PUSH_CONCURRENCY", "4"))
