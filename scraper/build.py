"""Pipeline utama: scrape ileague.id -> tabel CSV di data/.

Jalankan:  python -m scraper.build            (inkremental)
           python -m scraper.build --full-refresh
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from . import http, parse, transform

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CACHE = DATA / "cache" / "matches"
WIB = timezone(timedelta(hours=7))

MAX_WEEK = 34
# Laga yang selesai dalam N hari terakhir di-scrape ulang: operator sering
# melengkapi kartu/linimasa sehari-dua setelah laga.
RECHECK_DAYS = 4

# Statistik leaderboard yang diambil per klub (kode = value di dropdown situs)
LEADER_STATS = {
    "ASSIST": "assists",
    "SAVES": "saves",
    "CREATE_CHANCE": "chances_created",
    "SHOOT_ON_TARGET": "shots_on_target",
    "SHOOT_OFF_TARGET": "shots_off_target",
    "PASSING": "passes",
    "TACKLE": "tackles",
    "INTERCEPT": "interceptions",
    "CLEARANCE": "clearances",
    "DRIBBLE_SUCCESS": "successful_dribbles",
    "BALL_RECOVERY": "ball_recoveries",
    # Untuk validasi silang dengan data per laga:
    "GOAL": "goals_official",
    "YELLOW_CARD": "yellow_cards_official",
}

log = logging.getLogger("isl")


# ---------------------------------------------------------------------------
# Tahap 1: jadwal
# ---------------------------------------------------------------------------

def scrape_fixtures() -> pd.DataFrame:
    rows: list[dict] = []
    for wk in range(1, MAX_WEEK + 1):
        week_rows = parse.parse_fixtures(http.get(http.fixtures_path(wk)), wk)
        log.info("Pekan %d: %d laga", wk, len(week_rows))
        rows.extend(week_rows)
    df = pd.DataFrame(rows)
    if df.empty:
        raise RuntimeError("Tidak ada laga terbaca dari halaman jadwal — struktur situs mungkin berubah.")
    # Laga yang dijadwal ulang bisa muncul di dua pekan: pakai pekan asli
    # (terkecil) dan simpan daftar pekan tempat laga tercantum.
    return dedupe_fixtures(df)


# ---------------------------------------------------------------------------
# Tahap 2: detail laga (dengan cache)
# ---------------------------------------------------------------------------

def _cache_path(match_id: str) -> Path:
    return CACHE / f"{match_id}.json"


def needs_detail(row: pd.Series, full_refresh: bool, today: date) -> bool:
    if row["status"] != "FT":
        return False
    p = _cache_path(row["match_id"])
    if full_refresh or not p.exists():
        return True
    cached = json.loads(p.read_text())
    if cached.get("home_score") != row["home_score"] or cached.get("away_score") != row["away_score"]:
        return True
    if not cached.get("cards_consistent", True) or not cached.get("appearances"):
        played = date.fromisoformat(row["match_date"])
        return (today - played).days <= 14  # terus coba lengkapi selama 2 minggu
    played = date.fromisoformat(row["match_date"])
    return (today - played).days <= RECHECK_DAYS


def scrape_details(fixtures: pd.DataFrame, full_refresh: bool) -> int:
    CACHE.mkdir(parents=True, exist_ok=True)
    today = datetime.now(WIB).date()
    todo = fixtures[fixtures.apply(needs_detail, axis=1, full_refresh=full_refresh, today=today)]
    log.info("Detail laga yang di-scrape: %d", len(todo))
    for _, row in todo.iterrows():
        try:
            detail = parse.parse_match(http.get(row["url"]))
        except Exception as exc:  # satu laga gagal tidak boleh menggagalkan semua
            log.warning("Gagal parse %s: %s", row["match_id"], exc)
            continue
        detail["match_id"] = row["match_id"]
        detail["scraped_at"] = datetime.now(WIB).isoformat(timespec="seconds")
        _cache_path(row["match_id"]).write_text(json.dumps(detail, ensure_ascii=False, indent=1))
    return len(todo)


def load_details(fixtures: pd.DataFrame) -> dict[str, dict]:
    out = {}
    for mid in fixtures.loc[fixtures["status"] == "FT", "match_id"]:
        p = _cache_path(mid)
        if p.exists():
            out[mid] = json.loads(p.read_text())
    return out


# ---------------------------------------------------------------------------
# Tahap 3: leaderboard resmi per klub
# ---------------------------------------------------------------------------

def scrape_leaderboards() -> pd.DataFrame:
    page = http.get(http.leaderboard_path())
    clubs = parse.parse_club_options(page)
    if not clubs:
        raise RuntimeError("Daftar klub di halaman statistik tidak terbaca.")
    rows = []
    for code, col in LEADER_STATS.items():
        for club_id, club in clubs.items():
            html = http.post(http.leaderboard_path(), {"statistik": code, "klub": club_id})
            for r in parse.parse_leaderboard(html):
                rows.append({"stat_code": code, "stat": col, "team": club, "club_id": club_id, **r})
        log.info("Leaderboard %s selesai", code)
    df = pd.DataFrame(rows)
    df["snapshot_at"] = datetime.now(WIB).isoformat(timespec="seconds")
    return df


# ---------------------------------------------------------------------------
# Tahap 4: susun tabel
# ---------------------------------------------------------------------------

def scrape_clubs() -> pd.DataFrame:
    return pd.DataFrame(parse.parse_clubs(http.get(f"/home/index/{http.COMPETITION}")))


def build_clubs(clubs: pd.DataFrame | None, leaders: pd.DataFrame | None,
                official_table: pd.DataFrame) -> pd.DataFrame:
    """Satu baris per klub: ID, logo (2 ukuran), situs resmi, posisi klasemen."""
    if clubs is None or clubs.empty:
        # Cadangan: ID klub dari dropdown leaderboard (logo memakai ID yang sama)
        if leaders is None or leaders.empty:
            return pd.DataFrame(columns=["team", "club_id", "logo_url_small", "logo_url_large", "website"])
        clubs = leaders[["team", "club_id"]].drop_duplicates("team").assign(website=None)
    clubs = clubs.copy()
    clubs["club_id"] = clubs["club_id"].astype(str)
    logos = clubs["club_id"].map(parse.logo_urls).apply(pd.Series)
    clubs = pd.concat([clubs.reset_index(drop=True), logos.reset_index(drop=True)], axis=1)
    if len(official_table):
        clubs = clubs.merge(official_table[["team", "position", "points"]], on="team", how="left")
    cols = [c for c in ["team", "club_id", "logo_url_small", "logo_url_large", "website", "position", "points"] if c in clubs]
    return clubs[cols].sort_values("team").reset_index(drop=True)


def build_tables(fixtures: pd.DataFrame, details: dict[str, dict],
                 official_table: pd.DataFrame, leaders: pd.DataFrame | None,
                 clubs: pd.DataFrame | None = None) -> dict[str, pd.DataFrame]:
    m = fixtures.copy()
    # Lengkapi kolom jadwal dengan info dari halaman detail
    for col in ("kickoff_wib", "city", "attendance", "attendance_pct"):
        if col not in m:
            m[col] = None
    for col in ("referee", "var", "match_commissioner"):
        m[col] = None
    for i, row in m.iterrows():
        d = details.get(row["match_id"])
        if not d:
            continue
        for col in ("kickoff_wib", "city", "attendance", "attendance_pct", "venue"):
            if d.get(col) not in (None, ""):
                m.at[i, col] = d[col]
        for col in ("referee", "var", "match_commissioner"):
            m.at[i, col] = d.get("officials", {}).get(col)
    m["season"] = "2026-27"
    m["result"] = None
    ft = m["status"] == "FT"
    m.loc[ft, "result"] = m.loc[ft].apply(
        lambda r: "H" if r.home_score > r.away_score else "A" if r.away_score > r.home_score else "D", axis=1)
    m["total_goals"] = m["home_score"].fillna(0) + m["away_score"].fillna(0)
    m.loc[~ft, "total_goals"] = None
    match_cols = ["match_id", "season", "matchweek", "listed_in_weeks", "match_date", "kickoff_wib",
                  "status", "home_team", "away_team", "home_score", "away_score", "result", "total_goals",
                  "venue", "city", "attendance", "attendance_pct", "broadcaster",
                  "referee", "var", "match_commissioner", "url"]
    matches = m[match_cols].sort_values(["match_date", "kickoff_wib", "match_id"]).reset_index(drop=True)

    week_of = matches.set_index("match_id")["matchweek"].to_dict()
    date_of = matches.set_index("match_id")["match_date"].to_dict()

    goals, cards, apps, tstats, quality = [], [], [], [], []
    for mid, d in details.items():
        base = {"match_id": mid, "matchweek": week_of.get(mid), "match_date": date_of.get(mid)}
        for g in d["goals"]:
            opponent = d["away_team"] if g["side"] == "home" else d["home_team"]
            goals.append({**base, **g, "opponent": opponent})
        for c in d["cards"]:
            cards.append({**base, **{k: c[k] for k in ("team", "side", "player", "minute", "minute_num", "card")},
                          "source": d.get("cards_source")})
        for a in d["appearances"]:
            apps.append({**base, **a})
        for side in ("home", "away"):
            team = d[f"{side}_team"]
            opp = d["away_team" if side == "home" else "home_team"]
            tstats.append({**base, "team": team, "opponent": opp, "venue_type": side, **d["team_stats"][side]})

        # Validasi
        for side in ("home", "away"):
            n = sum(1 for g in d["goals"] if g["side"] == side)
            if n != d[f"{side}_score"]:
                quality.append({**base, "check": "goals_vs_score", "detail": f"{side}: {n} gol tercatat vs skor {d[f'{side}_score']}"})
        if not d.get("cards_consistent", True):
            quality.append({**base, "check": "cards_vs_team_stats", "detail": f"sumber={d.get('cards_source')}"})
        if not d["appearances"]:
            quality.append({**base, "check": "lineup_missing", "detail": "susunan pemain kosong"})

    goals_df = pd.DataFrame(goals)
    cards_df = pd.DataFrame(cards, columns=["match_id", "matchweek", "match_date", "team", "side",
                                            "player", "minute", "minute_num", "card", "source"])
    apps_df = pd.DataFrame(apps)
    tstats_df = pd.DataFrame(tstats)

    team_rows = transform.team_match_rows(matches)
    deductions = dict(zip(official_table["team"], official_table["points_deduction"])) if len(official_table) else {}

    leaders_wide = None
    if leaders is not None and len(leaders):
        leaders_wide = (leaders.pivot_table(index=["player", "team"], columns="stat", values="value", aggfunc="max")
                        .reset_index())
        leaders_wide.columns.name = None

    pstats = transform.player_season_stats(apps_df, goals_df, cards_df, team_rows, leaders_wide)

    # Link profil resmi pemain (slug dari leaderboard; pemain yang tidak masuk
    # top-10 kategori mana pun di klubnya belum punya link)
    if leaders is not None and len(leaders) and "player_slug" in leaders:
        slugs = (leaders.dropna(subset=["player_slug"]).drop_duplicates(["player", "team"])
                 .set_index(["player", "team"])["player_slug"])
        key = list(zip(pstats["player"], pstats["team"]))
        pstats["profile_url"] = [
            f"https://ileague.id/clubs/singleplayer/{http.COMPETITION}/{slugs[k]}" if k in slugs.index else None
            for k in key
        ]

    # Validasi silang gol per pemain vs leaderboard resmi
    if "goals_official" in pstats:
        # Leaderboard resmi ikut menghitung gol bunuh diri sebagai gol si pemain
        og = goals_df[goals_df["goal_type"] == "own_goal"].groupby("player").size() if len(goals_df) else pd.Series(dtype=int)
        pstats["own_goals"] = pstats["player"].map(og).fillna(0).astype(int)
        diff = pstats[(pstats["goals_official"] > 0)
                      & (pstats["goals_official"] != pstats["goals"] + pstats["own_goals"])]
        for _, r in diff.iterrows():
            quality.append({"match_id": None, "matchweek": None, "match_date": None,
                            "check": "player_goals_vs_official",
                            "detail": f"{r.player} ({r.team}): {r.goals} dari data laga vs {r.goals_official} resmi"})

    computed = transform.standings(team_rows, deductions)
    if len(official_table):
        cmp = computed.merge(official_table[["team", "points", "played"]], on="team", suffixes=("", "_official"))
        for _, r in cmp[(cmp.points != cmp.points_official) | (cmp.played != cmp.played_official)].iterrows():
            quality.append({"match_id": None, "matchweek": None, "match_date": None, "check": "standings_vs_official",
                            "detail": f"{r.team}: {r.points} poin/{r.played} main vs resmi {r.points_official}/{r.played_official}"})

    tables = {
        "clubs": build_clubs(clubs, leaders, official_table),
        "matches": matches,
        "team_match_results": team_rows,
        "team_match_stats": tstats_df,
        "goals": goals_df,
        "cards": cards_df,
        "appearances": apps_df,
        "player_season_stats": pstats,
        "standings_official": official_table,
        "standings_by_week": transform.standings_by_week(team_rows, deductions),
        "clean_sheets_team": transform.team_clean_sheets(team_rows),
        "clean_sheets_gk": transform.gk_clean_sheets(pstats, apps_df),
        "data_quality": pd.DataFrame(quality, columns=["match_id", "matchweek", "match_date", "check", "detail"]),
    }
    if leaders is not None:
        tables["player_leaderboard"] = leaders
    return tables


def dedupe_fixtures(df: pd.DataFrame) -> pd.DataFrame:
    df["listed_in_weeks"] = df.groupby("match_id")["matchweek"].transform(
        lambda s: ",".join(str(x) for x in sorted(set(s))))
    return df.sort_values("matchweek").drop_duplicates("match_id", keep="first").reset_index(drop=True)


def load_seed(path: Path) -> tuple[pd.DataFrame, dict, pd.DataFrame, pd.DataFrame]:
    """Muat hasil ekstraksi dari browser (skema sama dengan output parser).

    Dipakai untuk pengisian awal ketika situs tidak bisa dijangkau langsung
    dari mesin yang menjalankan pipeline.
    """
    seed = json.loads(Path(path).read_text(encoding="utf-8"))
    fixtures = dedupe_fixtures(pd.DataFrame(seed["fixtures"]))
    CACHE.mkdir(parents=True, exist_ok=True)
    for mid, d in seed["details"].items():
        _cache_path(mid).write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    leaders = pd.DataFrame(seed["leaders"])
    leaders["snapshot_at"] = seed.get("extracted_at")
    return fixtures, load_details(fixtures), pd.DataFrame(seed["table"]), leaders


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--full-refresh", action="store_true")
    ap.add_argument("--skip-leaderboards", action="store_true")
    ap.add_argument("--seed", type=Path, help="File JSON hasil ekstraksi browser (tanpa akses jaringan)")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    DATA.mkdir(exist_ok=True)

    clubs = None
    if args.seed:
        fixtures, details, official, leaders = load_seed(args.seed)
    else:
        try:
            clubs = scrape_clubs()
        except Exception as exc:  # logo tidak wajib; jangan gagalkan run
            log.warning("Daftar klub/logo gagal diambil: %s", exc)
        fixtures = scrape_fixtures()
        n_new = scrape_details(fixtures, args.full_refresh)
        details = load_details(fixtures)
        official = pd.DataFrame(parse.parse_table(http.get(f"/table/index/{http.COMPETITION}")))

        lb_path = DATA / "player_leaderboard.csv"
        if args.skip_leaderboards:
            leaders = pd.read_csv(lb_path) if lb_path.exists() else None
        elif n_new or args.full_refresh or not lb_path.exists():
            leaders = scrape_leaderboards()
        else:
            log.info("Tidak ada laga baru — leaderboard tidak di-scrape ulang.")
            leaders = pd.read_csv(lb_path)

    tables = build_tables(fixtures, details, official, leaders, clubs)
    for name, df in tables.items():
        df.to_csv(DATA / f"{name}.csv", index=False)
        log.info("Tulis %s.csv (%d baris)", name, len(df))

    ft = tables["matches"]["status"].eq("FT")
    meta = {
        "updated_at": datetime.now(WIB).isoformat(timespec="seconds"),
        "matches_played": int(ft.sum()),
        "matches_total_listed": int(len(tables["matches"])),
        "latest_completed_matchweek": int(tables["matches"].loc[ft, "matchweek"].max()) if ft.any() else None,
        "data_quality_issues": int(len(tables["data_quality"])),
        "source": "https://ileague.id",
    }
    (DATA / "last_updated.json").write_text(json.dumps(meta, indent=2))
    log.info("Selesai: %s", meta)
    return 0


if __name__ == "__main__":
    sys.exit(main())
