"""Tabel turunan untuk dashboard. Fungsi murni (DataFrame in -> DataFrame out)."""
from __future__ import annotations

import pandas as pd

# Kiper dihitung clean sheet jika timnya tidak kebobolan dan ia bermain >= 60 menit
GK_CLEAN_SHEET_MIN_MINUTES = 60


def team_match_rows(matches: pd.DataFrame) -> pd.DataFrame:
    """Satu baris per tim per laga (format 'long'), hanya laga selesai."""
    ft = matches[matches["status"] == "FT"].copy()
    home = pd.DataFrame(
        {
            "match_id": ft["match_id"], "matchweek": ft["matchweek"], "match_date": ft["match_date"],
            "team": ft["home_team"], "opponent": ft["away_team"], "venue_type": "home",
            "goals_for": ft["home_score"], "goals_against": ft["away_score"],
        }
    )
    away = pd.DataFrame(
        {
            "match_id": ft["match_id"], "matchweek": ft["matchweek"], "match_date": ft["match_date"],
            "team": ft["away_team"], "opponent": ft["home_team"], "venue_type": "away",
            "goals_for": ft["away_score"], "goals_against": ft["home_score"],
        }
    )
    rows = pd.concat([home, away], ignore_index=True)
    rows["goals_for"] = rows["goals_for"].astype(int)
    rows["goals_against"] = rows["goals_against"].astype(int)
    rows["result"] = (rows["goals_for"] - rows["goals_against"]).map(lambda d: "W" if d > 0 else "D" if d == 0 else "L")
    rows["points"] = rows["result"].map({"W": 3, "D": 1, "L": 0})
    rows["clean_sheet"] = (rows["goals_against"] == 0).astype(int)
    return rows.sort_values(["match_date", "match_id", "venue_type"]).reset_index(drop=True)


def _rank(table: pd.DataFrame) -> pd.DataFrame:
    # Pendekatan tie-break: poin, selisih gol, gol memasukkan.
    # Regulasi resmi bisa memakai head-to-head; klasemen resmi disimpan terpisah.
    table = table.sort_values(["points", "goal_diff", "goals_for", "team"], ascending=[False, False, False, True])
    table["position"] = range(1, len(table) + 1)
    return table


def standings(team_rows: pd.DataFrame, deductions: dict[str, int] | None = None,
              teams: list[str] | None = None) -> pd.DataFrame:
    deductions = deductions or {}
    agg = team_rows.groupby("team").agg(
        played=("result", "size"),
        won=("result", lambda s: (s == "W").sum()),
        drawn=("result", lambda s: (s == "D").sum()),
        lost=("result", lambda s: (s == "L").sum()),
        goals_for=("goals_for", "sum"),
        goals_against=("goals_against", "sum"),
        clean_sheets=("clean_sheet", "sum"),
        raw_points=("points", "sum"),
    )
    if teams:
        agg = agg.reindex(sorted(set(teams) | set(agg.index)), fill_value=0)
    agg = agg.reset_index().rename(columns={"index": "team"})
    agg["goal_diff"] = agg["goals_for"] - agg["goals_against"]
    agg["points_deduction"] = agg["team"].map(deductions).fillna(0).astype(int)
    agg["points"] = agg["raw_points"] + agg["points_deduction"]
    cols = ["position", "team", "played", "won", "drawn", "lost", "goals_for", "goals_against",
            "goal_diff", "clean_sheets", "points_deduction", "points"]
    return _rank(agg)[cols].reset_index(drop=True)


def standings_by_week(team_rows: pd.DataFrame, deductions: dict[str, int] | None = None) -> pd.DataFrame:
    """Klasemen kumulatif setelah tiap pekan (untuk grafik 'perjalanan posisi').

    Pengurangan poin diterapkan di semua pekan karena tanggal sanksinya tidak
    dipublikasikan di data jadwal.
    """
    teams = sorted(team_rows["team"].unique())
    out = []
    for wk in sorted(team_rows["matchweek"].unique()):
        t = standings(team_rows[team_rows["matchweek"] <= wk], deductions, teams)
        t.insert(0, "after_matchweek", int(wk))
        out.append(t)
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()


def player_season_stats(appearances: pd.DataFrame, goals: pd.DataFrame, cards: pd.DataFrame,
                        team_rows: pd.DataFrame, leaders_wide: pd.DataFrame | None) -> pd.DataFrame:
    apps = appearances.copy()
    played = apps[apps["minutes_played"] > 0]
    base = played.groupby(["player", "team"]).agg(
        appearances=("match_id", "nunique"),
        starts=("is_starter", "sum"),
        minutes=("minutes_played", "sum"),
    ).reset_index()

    # Posisi dominan (yang paling sering dimainkan sebagai starter)
    pos = (apps[apps["is_starter"] & apps["position_group"].notna()]
           .groupby(["player", "team"])["position_group"].agg(lambda s: s.mode().iat[0]).rename("position_group"))
    base = base.merge(pos, on=["player", "team"], how="left")
    # Pemain yang belum pernah jadi starter: ambil posisi dari leaderboard tidak
    # tersedia, jadi tandai sebagai cadangan
    base["position_group"] = base["position_group"].fillna("SUB_ONLY")

    g = goals[goals["goal_type"] != "own_goal"]
    gstats = g.groupby(["player", "team"]).agg(
        goals=("goal_type", "size"),
        penalty_goals=("goal_type", lambda s: (s == "penalty").sum()),
    ).reset_index()
    base = base.merge(gstats, on=["player", "team"], how="left")

    if len(cards):
        c = cards.copy()
        c["yellow"] = c["card"].isin(["yellow", "second_yellow"]).astype(int)
        c["red"] = c["card"].isin(["red", "second_yellow"]).astype(int)
        cstats = c.groupby(["player", "team"])[["yellow", "red"]].sum().reset_index()
        cstats.columns = ["player", "team", "yellow_cards", "red_cards"]
        base = base.merge(cstats, on=["player", "team"], how="left")
    else:
        base["yellow_cards"] = 0
        base["red_cards"] = 0

    # Clean sheet kiper
    gk = apps[(apps["position_group"] == "GK") | (apps["position"] == "GK")]
    gk = gk[gk["minutes_played"] >= GK_CLEAN_SHEET_MIN_MINUTES]
    conceded = team_rows[["match_id", "team", "goals_against"]]
    gk = gk.merge(conceded, on=["match_id", "team"], how="left")
    gk_cs = (gk.assign(cs=(gk["goals_against"] == 0).astype(int))
               .groupby(["player", "team"])["cs"].sum().rename("clean_sheets").reset_index())
    base = base.merge(gk_cs, on=["player", "team"], how="left")

    if leaders_wide is not None and len(leaders_wide):
        base = base.merge(leaders_wide, on=["player", "team"], how="left")

    num_cols = base.select_dtypes("number").columns
    base[num_cols] = base[num_cols].fillna(0).astype(int)
    base["goals_per_90"] = (base["goals"] * 90 / base["minutes"].where(base["minutes"] > 0)).round(2).fillna(0)
    if "assists" in base:
        base["goal_contributions"] = base["goals"] + base["assists"]
    return base.sort_values(["goals", "minutes"], ascending=[False, True]).reset_index(drop=True)


def gk_clean_sheets(player_stats: pd.DataFrame, appearances: pd.DataFrame) -> pd.DataFrame:
    gk_names = appearances[(appearances["position_group"] == "GK") | (appearances["position"] == "GK")][["player", "team"]].drop_duplicates()
    out = player_stats.merge(gk_names, on=["player", "team"], how="inner")
    cols = [c for c in ["player", "team", "appearances", "minutes", "clean_sheets", "saves"] if c in out]
    out = out[cols].sort_values(["clean_sheets", "minutes"], ascending=[False, True]).reset_index(drop=True)
    out.insert(0, "rank", out["clean_sheets"].rank(method="min", ascending=False).astype(int))
    return out


def team_clean_sheets(team_rows: pd.DataFrame) -> pd.DataFrame:
    t = team_rows.groupby("team").agg(
        played=("match_id", "size"), clean_sheets=("clean_sheet", "sum"),
        goals_against=("goals_against", "sum")).reset_index()
    t = t.sort_values(["clean_sheets", "goals_against"], ascending=[False, True]).reset_index(drop=True)
    t.insert(0, "rank", t["clean_sheets"].rank(method="min", ascending=False).astype(int))
    return t
