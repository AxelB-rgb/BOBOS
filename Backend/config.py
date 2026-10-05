from pathlib import Path
import os

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "Data"
DB_PATH = Path(__file__).resolve().parent / "bos.db"
IFC_PATH = DATA_DIR / "V7-CAMPUS-DIJON-BATIMENT.ifc"

# HVAC considéré actif au-dessus du bruit / veille des ventilos (~0.03 kWh/h).
MIN_HVAC_KWH_PER_HOUR = 0.08
MIN_LIGHTING_KWH_PER_HOUR = 0.05
MIN_DURATION_HOURS = 2
DEFAULT_LIMIT = 200

# Simple .env loader; existing environment variables take precedence.
if (ROOT / ".env").exists():
    for line in (ROOT / ".env").read_text(encoding="utf-8-sig").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")
ENERGY_PRICE = float(os.getenv("ENERGY_PRICE_EUR_KWH", "0.20"))
AREA_PER_PERSON = float(os.getenv("AREA_PER_PERSON_M2", "4"))
if ENERGY_PRICE <= 0 or AREA_PER_PERSON <= 0:
    raise ValueError("Le tarif et la surface par personne doivent être positifs.")

LLM_PROVIDER = os.getenv("LLM_PROVIDER", "auto").lower()
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2:latest")
LLM_TIMEOUT_SECONDS = float(os.getenv("LLM_TIMEOUT_SECONDS", "180"))
if LLM_PROVIDER not in {"auto", "local", "openai", "ollama"}:
    raise ValueError("LLM_PROVIDER doit être auto, local, openai ou ollama.")
