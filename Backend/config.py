from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "Data"
DB_PATH = Path(__file__).resolve().parent / "bos.db"
IFC_PATH = DATA_DIR / "V7-CAMPUS-DIJON-BATIMENT.ifc"

# HVAC considéré actif au-dessus du bruit / veille des ventilos (~0.03 kWh/h).
MIN_HVAC_KWH_PER_HOUR = 0.08
MIN_LIGHTING_KWH_PER_HOUR = 0.05
MIN_DURATION_HOURS = 2
DEFAULT_LIMIT = 200
