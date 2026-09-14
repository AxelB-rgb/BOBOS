from pathlib import Path
import sqlite3

from .config import DB_PATH

SCHEMA_PATH = Path(__file__).resolve().parent / "sql" / "schema.sql"


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path or DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=120)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=120000")
    conn.execute("PRAGMA journal_mode=DELETE")
    conn.execute("PRAGMA synchronous=OFF")
    conn.execute("PRAGMA temp_store=MEMORY")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        DROP TABLE IF EXISTS energy_hourly;
        DROP TABLE IF EXISTS occupancy_hourly;
        DROP TABLE IF EXISTS rooms;
        """
    )
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.commit()
