"""Salin CSV di data/ ke Google Sheets (satu worksheet per tabel).

Looker Studio dan Tableau Public membaca spreadsheet ini.
Butuh env:
  GOOGLE_SERVICE_ACCOUNT_JSON  isi file JSON kunci service account
  GOOGLE_SHEET_ID              ID spreadsheet (bagian URL di antara /d/ dan /edit)
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time

import gspread
import pandas as pd

from .build import DATA

# Tabel yang dikirim ke Sheets (urutan = urutan tab)
SHEET_TABLES = [
    "clubs",
    "matches",
    "standings_official",
    "standings_by_week",
    "team_match_results",
    "team_match_stats",
    "player_season_stats",
    "goals",
    "cards",
    "appearances",
    "clean_sheets_gk",
    "clean_sheets_team",
    "player_leaderboard",
    "data_quality",
]

log = logging.getLogger("isl.sheets")


def _values(df: pd.DataFrame) -> list[list]:
    df = df.astype(object).where(df.notna(), "")
    return [list(df.columns)] + df.values.tolist()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    creds = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
    gc = gspread.service_account_from_dict(creds)
    sh = gc.open_by_key(os.environ["GOOGLE_SHEET_ID"])
    existing = {ws.title: ws for ws in sh.worksheets()}

    for name in SHEET_TABLES + ["last_updated"]:
        if name == "last_updated":
            meta = json.loads((DATA / "last_updated.json").read_text())
            df = pd.DataFrame([meta])
        else:
            path = DATA / f"{name}.csv"
            if not path.exists():
                continue
            df = pd.read_csv(path)
        values = _values(df)
        rows, cols = len(values), max(len(values[0]), 1)
        ws = existing.get(name) or sh.add_worksheet(title=name, rows=rows + 10, cols=cols + 2)
        ws.clear()
        ws.resize(rows=max(rows, 2), cols=cols)
        ws.update(values, "A1", value_input_option="USER_ENTERED")
        log.info("Sheet %s: %d baris", name, rows - 1)
        time.sleep(1.5)  # hormati kuota API Sheets
    return 0


if __name__ == "__main__":
    sys.exit(main())
