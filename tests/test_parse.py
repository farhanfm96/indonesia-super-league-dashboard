from pathlib import Path

from scraper import parse

FX = Path(__file__).parent / "fixtures"


def test_fixtures_week7():
    rows = parse.parse_fixtures((FX / "fixtures_week7.html").read_text(), 7)
    assert len(rows) == 3
    arema = next(r for r in rows if r["home_team"] == "AREMA FC")
    assert arema["status"] == "FT" and (arema["home_score"], arema["away_score"]) == (4, 0)
    assert arema["match_date"] == "2026-10-25"
    assert arema["broadcaster"] == "EXCLUSIVE VIDIO"
    pss = next(r for r in rows if r["away_team"] == "BORNEO FC SAMARINDA")
    assert pss["home_team"] == "PSS SLEMAN"  # spasi ekor di situs dibersihkan
    assert pss["status"] == "SCHEDULED" and pss["kickoff_wib"] == "15:30"
    assert pss["match_id"] == "PSS_SLEMAN-vs-BORNEO_FC_SAMARINDA"


def test_match_detail_persija_persik():
    d = parse.parse_match((FX / "match_persija_persik.html").read_text())
    assert (d["home_team"], d["away_team"], d["home_score"], d["away_score"]) == ("PERSIJA JAKARTA", "PERSIK KEDIRI", 2, 1)
    assert d["match_date"] == "2026-10-26" and d["kickoff_wib"] == "19:00"
    assert d["venue"] == "Jakarta International Stadium" and d["city"] == "Jakarta Utara"
    assert d["attendance"] == 15872 and d["attendance_pct"] == 19
    assert d["officials"]["referee"] == "STEVEN YUBEL POLI"

    # Gol diambil dari header, bukan linimasa (linimasa memuat gol "hantu" Ivan Mamut 96')
    assert [(g["player"], g["minute"], g["side"]) for g in d["goals"]] == [
        ("DENIS KOLINGER", "64", "home"), ("RIZKY RIDHO RAMADHANI", "69", "home"), ("IMANOL GARCIA LUGEA", "10", "away")]

    # Header belum memuat kartu -> linimasa dipakai dan cocok dengan statistik tim 3-5
    assert d["cards_source"] == "timeline" and d["cards_consistent"]
    assert sum(c["side"] == "home" for c in d["cards"]) == 3
    assert sum(c["side"] == "away" for c in d["cards"]) == 5

    apps = {(a["side"], a["player"]): a for a in d["appearances"]}
    assert apps[("home", "KERIM MEMIJA")]["minutes_played"] == 21
    assert apps[("home", "ILHAM RIO FAHMI")]["minutes_played"] == 69
    assert apps[("home", "IVAN MAMUT")]["minutes_played"] == 1
    assert apps[("home", "RADOVAN PANKOV")]["is_captain"]
    assert apps[("away", "HUSNA AL MALIK RIWANI SAPUTRA")]["position"] == "GK"
    assert apps[("home", "MUHAMAD HAFIZH RIZKIANUR")]["minutes_played"] == 0  # cadangan tak dimainkan
    assert sum(a["is_starter"] for a in d["appearances"] if a["side"] == "home") == 11

    assert d["team_stats"]["home"]["possession_pct"] == 57
    assert d["team_stats"]["away"]["passes_completed"] == 198


def test_minute_parsing():
    assert parse.parse_minute("90+1'") == ("90+1", 91)
    assert parse.parse_minute("45 + 2") == ("45+2", 47)
    assert parse.parse_minute("") == ("", None)
