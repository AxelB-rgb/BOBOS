"""Upgrade a derived database off-line, preserving rooms, presence and simulation history."""
from pathlib import Path
import os
import sqlite3
from contextlib import closing
from .config import DB_PATH, DATA_DIR
from .db import connect
from .electrical import load_meter_energy


def run():
    if not DB_PATH.exists():
        raise SystemExit('Importez la base avec python -m Backend.etl.')
    staging = DB_PATH.with_name('bos.electrical-staging.db')
    # Both paths are fixed children of the project backend directory.
    if staging.resolve().parent != DB_PATH.resolve().parent:
        raise RuntimeError('Chemin de préparation invalide.')
    with closing(sqlite3.connect(DB_PATH)) as original, closing(sqlite3.connect(staging)) as target:
        original.backup(target)
    conn = connect(staging)
    try:
        report = load_meter_energy(conn,DATA_DIR)
    finally:
        conn.close()
    os.replace(staging,DB_PATH)
    print('Référentiel électrique activé. Historique des simulations conservé.',flush=True)
    print(report,flush=True)


if __name__ == '__main__':
    run()
