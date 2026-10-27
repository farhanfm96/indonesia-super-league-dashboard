"""Parser HTML ileague.id -> dict/list Python.

Semua parser bekerja dari HTML string, tanpa akses jaringan, supaya bisa diuji
dengan sampel di tests/fixtures/.
"""
from __future__ import annotations

import re
from datetime import date
from urllib.parse import unquote

from bs4 import BeautifulSoup, Tag

BULAN = {
    "januari": 1, "februari": 2, "maret": 3, "april": 4, "mei": 5, "juni": 6,
    "juli": 7, "agustus": 8, "september": 9, "oktober": 10, "november": 11,
    "desember": 12,
}

DETAIL_RE = re.compile(
    r"/result/detail/(?P<comp>[^/]+)/(?P<date>\d{4}-\d{2}-\d{2})/(?P<home>[^/]+)/(?P<away>[^/?#]+)"
)
SCORE_RE = re.compile(r"(\d+)\s*:\s*(\d+)")
TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})$")
MINUTE_RE = re.compile(r"(\d+)(?:\s*\+\s*(\d+))?\s*'?")


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


def _txt(el: Tag | None) -> str:
    return re.sub(r"\s+", " ", el.get_text(" ")).strip() if el else ""


def clean_team(name: str) -> str:
    """Nama tim kanonik: huruf besar, tanpa spasi berlebih (situs menulis 'PSS SLEMAN ')."""
    return re.sub(r"\s+", " ", name).strip().upper()


def parse_id_date(text: str) -> date | None:
    """'Senin, 26 Oktober 2026' -> date(2026, 10, 26)."""
    m = re.search(r"(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})", text)
    if not m:
        return None
    bulan = BULAN.get(m.group(2).lower())
    return date(int(m.group(3)), bulan, int(m.group(1))) if bulan else None


def parse_minute(text: str) -> tuple[str, int | None]:
    """"90+1'" -> ("90+1", 91). Menit numerik dipakai untuk sorting/agregasi."""
    m = MINUTE_RE.search(text or "")
    if not m:
        return "", None
    base, extra = int(m.group(1)), m.group(2)
    label = f"{base}+{extra}" if extra else str(base)
    return label, base + (int(extra) if extra else 0)


def match_id_from_url(url: str) -> str:
    """ID laga = pasangan kandang-tandang. Dalam satu musim tiap pasangan hanya
    bertemu sekali sebagai tuan rumah, jadi ID ini tetap stabil walau laga
    dijadwal ulang (tanggal di URL berubah)."""
    m = DETAIL_RE.search(url)
    if not m:
        raise ValueError(f"URL detail laga tidak dikenali: {url}")
    return f"{m['home'].strip('_')}-vs-{m['away'].strip('_')}"


# ---------------------------------------------------------------------------
# Halaman jadwal per pekan: /fixtures/index/<COMP>/<pekan>/0
# ---------------------------------------------------------------------------

def parse_fixtures(html: str, week: int) -> list[dict]:
    soup = _soup(html)
    rows: list[dict] = []
    current_date: date | None = None

    container = soup.select_one("#myTabContent") or soup
    for el in container.find_all(["div", "a"], recursive=True):
        classes = el.get("class") or []
        # Header tanggal: <div class="col-md-12"> Sabtu, 24 Oktober 2026</div>
        if el.name == "div" and "col-md-12" in classes and not el.find("a"):
            d = parse_id_date(_txt(el))
            if d:
                current_date = d
            continue
        if el.name != "a" or "table-row" not in classes:
            continue
        href = el.get("href", "")
        if "/result/detail/" not in href:
            continue

        tds = el.select("tr td")
        bolds = [_txt(b) for b in el.select("td b")]
        badge = _txt(el.select_one(".badge"))
        venue = ""
        # Kolom venue = td tanpa <b>, tanpa badge, tanpa img, dengan teks
        for td in tds:
            if td.find(["b", "img"]) or td.select_one(".badge"):
                continue
            if _txt(td):
                venue = _txt(td)
        logo = el.select_one("img.broadcast-logo")
        broadcaster = ""
        if logo is not None:
            src = logo.get("src") or logo.get("alt") or ""
            fname = unquote(src.rsplit("/", 1)[-1].split("?")[0])
            broadcaster = re.sub(r"\.(png|jpe?g|svg|webp)$", "", fname, flags=re.I)

        m = DETAIL_RE.search(href)
        home = clean_team(bolds[0]) if bolds else clean_team(m["home"].replace("_", " "))
        away = clean_team(bolds[1]) if len(bolds) > 1 else clean_team(m["away"].replace("_", " "))

        status, home_score, away_score, kickoff = "SCHEDULED", None, None, None
        sm = SCORE_RE.search(badge)
        tm = TIME_RE.match(badge.strip())
        if badge.upper().startswith("FT") and sm:
            status, home_score, away_score = "FT", int(sm.group(1)), int(sm.group(2))
        elif tm:
            kickoff = f"{int(tm.group(1)):02d}:{tm.group(2)}"
        elif sm:  # skor tanpa FT -> sedang berlangsung
            status, home_score, away_score = "LIVE", int(sm.group(1)), int(sm.group(2))
        elif badge:
            status = badge.upper()  # mis. TUNDA / POSTPONED

        rows.append(
            {
                "match_id": match_id_from_url(href),
                "matchweek": week,
                "match_date": (current_date or date.fromisoformat(m["date"])).isoformat(),
                "url_date": m["date"],
                "kickoff_wib": kickoff,
                "home_team": home,
                "away_team": away,
                "home_score": home_score,
                "away_score": away_score,
                "status": status,
                "venue": venue,
                "broadcaster": broadcaster,
                "url": href if href.startswith("http") else "https://ileague.id" + href,
            }
        )
    return rows


# ---------------------------------------------------------------------------
# Halaman detail laga: /result/detail/<COMP>/<tgl>/<HOME>/<AWAY>
# ---------------------------------------------------------------------------

GROUP_MAP = {
    "PENJAGA GAWANG": "GK",
    "BELAKANG": "DEF",
    "GELANDANG": "MID",
    "PENYERANG": "FWD",
    "PEMAIN PENGGANTI": "SUB",
}
STAT_MAP = {
    "penguasaan bola": "possession_pct",
    "tembakan ke gawang": "shots_on_target",
    "total tembakan": "shots_total",
    "akurasi tembakan": "shot_accuracy_pct",
    "umpan sukses": "passes_completed",
    "umpan gagal": "passes_failed",
    "tendangan sudut": "corners",
    "tekel sukses": "tackles_won",
    "offside": "offsides",
    "pelanggaran": "fouls",
    "kartu kuning": "yellow_cards",
    "kartu merah": "red_cards",
}
OFFICIAL_MAP = {
    "PENGAWAS PERTANDINGAN": "match_commissioner",
    "WASIT": "referee",
    "ASISTEN WASIT 1": "assistant_referee_1",
    "ASISTEN WASIT 2": "assistant_referee_2",
    "WASIT CADANGAN": "fourth_official",
    "CADANGAN WASIT": "fourth_official",
    "VAR": "var",
    "AVAR": "avar",
}
GREEN = ("12ca20", "green")  # ikon refresh hijau = masuk lapangan
RED = ("e60000", "red")      # ikon refresh merah = keluar lapangan


def _style(el: Tag) -> str:
    return (el.get("style") or "").lower().replace(" ", "")


def _card_color(i: Tag) -> str | None:
    st = _style(i)
    if "yellow" in st:
        return "Y"
    if "red" in st:
        return "R"
    return None


def _hero_events(team_div: Tag) -> tuple[list[dict], list[dict]]:
    goals, cards = [], []
    for li in team_div.select("ul li"):
        icons = li.find_all("i")
        text = _txt(li)
        hm = re.match(r"(.+?)\s+(\d+(?:\s*\+\s*\d+)?)\s*'", text)
        if not hm:
            continue
        player = hm.group(1).strip().upper()
        label, num = parse_minute(hm.group(2))
        if any("fa-futbol" in " ".join(i.get("class", [])) for i in icons):
            goal_type = "penalty" if "(P)" in text.upper() else "own_goal" if "(OG)" in text.upper() else "open_play"
            goals.append({"player": player, "minute": label, "minute_num": num, "goal_type": goal_type})
            continue
        squares = [i for i in icons if "fa-square" in " ".join(i.get("class", []))]
        if squares:
            colors = [_card_color(i) for i in squares]
            if len(squares) >= 2:
                card = "second_yellow"
            elif "R" in colors:
                card = "red"
            else:
                card = "yellow"
            cards.append({"player": player, "minute": label, "minute_num": num, "card": card, "source": "header"})
    return goals, cards


def _timeline_cards(soup: BeautifulSoup) -> list[dict]:
    """Kartu dari linimasa. Sisi tim: div dengan offset-lg-2 = tim tamu."""
    tl = soup.select_one("#live_timeline")
    out, seen = [], set()
    if tl is None:
        return out
    for el in tl.find_all("div", recursive=False):
        cls = el.get("class") or []
        text = _txt(el)
        # alert-warning = kartu kuning, alert-danger = kartu merah
        if "alert-danger" in cls or "kartu merah" in text.lower():
            card = "red"
        elif "alert-warning" in cls or "kartu kuning" in text.lower():
            card = "yellow"
        else:
            continue
        a = el.find("a")
        if a is not None:
            player = _txt(a).upper()
        else:  # kadang nama pemain tidak berupa link
            pm = re.match(r"\d+(?:\+\d+)?'\s*(.+?)\s+mendapatkan", text)
            if not pm:
                continue
            player = pm.group(1).upper()
        label, num = parse_minute(text)
        side = "away" if "offset-lg-2" in cls else "home"
        key = (side, player, card)
        if key in seen:  # linimasa kadang menduplikasi event dengan menit berbeda
            continue
        seen.add(key)
        out.append({"side": side, "player": player, "minute": label, "minute_num": num, "card": card, "source": "timeline"})
    return out


def _lineup_tables(soup: BeautifulSoup) -> list[Tag]:
    lu = soup.select_one("#lineup")
    if lu is None:
        return []
    # Tabel daftar pemain bersarang di dalam tabel pembungkus; ambil yang terdalam
    return [t for t in lu.find_all("table")
            if "PENJAGA GAWANG" in _txt(t).upper() and t.find("table") is None]


def _pitch_positions(soup: BeautifulSoup) -> dict[str, dict[str, str]]:
    pos: dict[str, dict[str, str]] = {"home": {}, "away": {}}
    selectors = {
        "home": "#lineup section.pitch .home-team .player",
        "away": "#lineup section.pitch .visitor-team .player, #lineup section.pitch .away-team .player",
    }
    for side, sel in selectors.items():
        for sp in soup.select(sel):
            label = sp.get("aria-label") or ""
            m = re.match(r"(.+?),\s*posisi\s+(\S+)", label)
            if m:
                pos[side][m.group(1).strip().upper()] = m.group(2).upper()
            else:
                strong, small = sp.find("strong"), sp.find("small")
                if strong and small:
                    pos[side][_txt(strong).upper()] = _txt(small).upper()
    return pos


def _parse_lineup_table(table: Tag) -> list[dict]:
    players, group = [], None
    for tr in table.find_all("tr"):
        h4 = tr.find("h4")
        if h4:
            group = GROUP_MAP.get(_txt(h4).upper(), _txt(h4).upper())
            continue
        a = tr.find("a", class_="btn")
        if a is None:
            continue
        num_el = tr.select_one("span.btn-primary")
        shirt = _txt(num_el)
        # Nama = teks <a> tanpa isi <span> (ikon/menit/kapten) dan <p>
        a_copy = BeautifulSoup(str(a), "lxml").find("a")
        captain = False
        for sp in a_copy.find_all(["span", "p"]):
            if _txt(sp) == "C":
                captain = True
            sp.decompose()
        name = _txt(a_copy).upper()
        if not name:
            continue
        on_min = off_min = None
        goal_minutes = []
        for i in a.find_all("i"):
            cls = " ".join(i.get("class", []))
            # Menit ada di teks setelah ikon: <i ...></i>'64
            nxt = i.next_sibling
            mtxt = str(nxt) if nxt and isinstance(nxt, str) else ""
            _, m = parse_minute(mtxt)
            if "fa-futbol" in cls:
                goal_minutes.append(m)
            elif "fa-refresh" in cls:
                st = _style(i)
                if any(c in st for c in GREEN):
                    on_min = m
                elif any(c in st for c in RED):
                    off_min = m
        players.append(
            {
                "player": name,
                "shirt_no": int(shirt) if shirt.isdigit() else None,
                "position_group": group,
                "is_starter": group not in ("SUB", None),
                "is_captain": captain,
                "sub_on_min": on_min,
                "sub_off_min": off_min,
            }
        )
    return players


def _minutes_played(p: dict, red_minute: int | None, match_len: int = 90) -> int:
    if p["is_starter"]:
        start = 0
    elif p["sub_on_min"] is not None:
        start = p["sub_on_min"]
    else:
        return 0  # cadangan tidak dimainkan
    end = p["sub_off_min"]
    if end is None:
        end = red_minute if red_minute is not None else match_len
    end = min(end, match_len)
    # Pemain yang masuk di menit 90+ tetap dihitung tampil (minimal 1 menit)
    return max(end - start, 0 if p["is_starter"] else 1)


def parse_match(html: str) -> dict:
    soup = _soup(html)
    hero_teams = soup.select(".match-hero-team")
    if len(hero_teams) < 2:
        raise ValueError("Header laga tidak ditemukan")
    home = clean_team(_txt(hero_teams[0].select_one(".match-hero-name")))
    away = clean_team(_txt(hero_teams[1].select_one(".match-hero-name")))

    score_txt = _txt(soup.select_one(".result-match"))
    sm = SCORE_RE.search(score_txt)
    home_score, away_score = (int(sm.group(1)), int(sm.group(2))) if sm else (None, None)

    middle = soup.select_one(".result-match").parent
    status_txt = _txt(middle.find("div"))
    status = "FT" if status_txt.upper().startswith("FT") else (status_txt.upper() or None)

    kickoff = attendance = attendance_pct = None
    for c in soup.select(".live-time center"):
        t = _txt(c)
        if "Kick Off" in t:
            km = re.search(r"(\d{1,2}):(\d{2})", t)
            kickoff = f"{int(km.group(1)):02d}:{km.group(2)}" if km else None
        if "Penonton" in t:
            am = re.search(r":\s*([\d.]+)\s*(?:\((\d+)%\))?", t)
            if am:
                attendance = int(am.group(1).replace(".", ""))
                attendance_pct = int(am.group(2)) if am.group(2) else None

    match_date = venue = city = None
    for ul in soup.find_all("ul"):
        if ul.select_one(".fa-calendar") and ul.select_one(".fa-map-marker"):
            d = parse_id_date(_txt(ul.select_one(".fa-calendar").parent))
            match_date = d.isoformat() if d else None
            loc = _txt(ul.select_one(".fa-map-marker").parent)
            loc = re.sub(r"^Stadion\s+", "", loc)
            parts = [p.strip() for p in loc.split(",")]
            venue = parts[0] or None
            city = parts[1] if len(parts) > 1 and parts[1] else None
            break

    # Gol & kartu dari header
    home_goals, home_cards = _hero_events(hero_teams[0])
    away_goals, away_cards = _hero_events(hero_teams[1])
    goals = [dict(g, team=home, side="home") for g in home_goals] + \
            [dict(g, team=away, side="away") for g in away_goals]

    # Statistik tim (dibaca lebih dulu karena dipakai untuk validasi kartu)
    stats = {"home": {}, "away": {}}
    for li in soup.select("#matchstats li"):
        center = li.select_one(".center")
        if center is None:
            continue
        key = STAT_MAP.get(_txt(center).replace("%", "").strip().lower())
        # Urutan DOM: angka pertama = tuan rumah, terakhir = tamu
        # (class left/right di situs tidak konsisten, jadi diabaikan)
        vals = [s for s in li.find_all("span", recursive=False) if s is not center]
        if not key or len(vals) < 2:
            continue
        for side, s in (("home", vals[0]), ("away", vals[-1])):
            v = _txt(s)
            stats[side][key] = int(v) if v.lstrip("-").isdigit() else None

    # Kartu: header adalah sumber utama (cocok dengan statistik tim di ~90% laga),
    # tapi kadang kosong/terlambat diisi. Jika jumlahnya tidak cocok dengan
    # statistik tim, pakai linimasa.
    header_cards = [dict(c, team=home, side="home") for c in home_cards] + \
                   [dict(c, team=away, side="away") for c in away_cards]
    tl_cards = [dict(c, team=home if c["side"] == "home" else away) for c in _timeline_cards(soup)]

    def _counts(cs: list[dict]) -> dict:
        out = {}
        for side in ("home", "away"):
            y = sum(1 for c in cs if c["side"] == side and c["card"] in ("yellow", "second_yellow"))
            r = sum(1 for c in cs if c["side"] == side and c["card"] in ("red", "second_yellow"))
            out[side] = (y, r)
        return out

    expected = {s: (stats[s].get("yellow_cards"), stats[s].get("red_cards")) for s in ("home", "away")}
    if _counts(header_cards) == expected:
        cards, cards_source, cards_consistent = header_cards, "header", True
    elif tl_cards and _counts(tl_cards) == expected:
        cards, cards_source, cards_consistent = tl_cards, "timeline", True
    else:
        # Tidak ada sumber yang cocok sempurna: ambil yang lebih lengkap, tandai
        cards, cards_source = (tl_cards, "timeline") if len(tl_cards) > len(header_cards) else (header_cards, "header")
        cards_consistent = False

    # Susunan pemain + menit bermain
    tables = _lineup_tables(soup)
    positions = _pitch_positions(soup)
    appearances = []
    for side, team, table in zip(("home", "away"), (home, away), tables):
        for p in _parse_lineup_table(table):
            red = next((c["minute_num"] for c in cards
                        if c["side"] == side and c["player"] == p["player"]
                        and c["card"] in ("red", "second_yellow")), None)
            p.update(
                team=team,
                side=side,
                position=positions[side].get(p["player"]) or (p["position_group"] if p["is_starter"] else None),
                minutes_played=_minutes_played(p, red),
            )
            appearances.append(p)

    # Perangkat pertandingan
    officials = {}
    for td in soup.select("#perangkat td"):
        b = td.find("b")
        if not b:
            continue
        label = _txt(b).upper()
        name = _txt(td)[len(_txt(b)):].strip()
        name = re.sub(r"\s*Histori$", "", name).strip()
        key = OFFICIAL_MAP.get(label)
        if key and name:
            officials[key] = name.upper()

    return {
        "home_team": home,
        "away_team": away,
        "home_score": home_score,
        "away_score": away_score,
        "status": status,
        "match_date": match_date,
        "kickoff_wib": kickoff,
        "venue": venue,
        "city": city,
        "attendance": attendance,
        "attendance_pct": attendance_pct,
        "officials": officials,
        "goals": goals,
        "cards": cards,
        "cards_source": cards_source,
        "cards_consistent": cards_consistent,
        "appearances": appearances,
        "team_stats": stats,
    }


# ---------------------------------------------------------------------------
# Klasemen resmi: /table/index/<COMP>
# ---------------------------------------------------------------------------

def parse_table(html: str) -> list[dict]:
    soup = _soup(html)
    table = soup.select_one("table.result-point")
    out: list[dict] = []
    if table is None:
        return out
    for tr in table.find_all("tr"):
        cells = [_txt(td) for td in tr.find_all("td")]
        if len(cells) < 10 or not cells[0].isdigit():
            continue
        name = cells[1]
        dm = re.search(r"\((-\d+)\)", name)
        deduction = int(dm.group(1)) if dm else 0
        name = clean_team(re.sub(r"\(-?\d+\)", "", name))
        nums = [int(c) for c in cells[2:10]]
        out.append(
            {
                "position": int(cells[0]),
                "team": name,
                "played": nums[0], "won": nums[1], "drawn": nums[2], "lost": nums[3],
                "goals_for": nums[4], "goals_against": nums[5], "goal_diff": nums[6],
                "points": nums[7],
                "points_deduction": deduction,
                "form": cells[10] if len(cells) > 10 else "",
            }
        )
    return out


# ---------------------------------------------------------------------------
# Leaderboard pemain: POST /top_actions/player/<COMP> statistik=<STAT>&klub=<id>
# ---------------------------------------------------------------------------

def parse_leaderboard(html: str) -> list[dict]:
    soup = _soup(html)
    table = soup.select_one("table.background-color-2")
    out: list[dict] = []
    if table is None:
        return out
    for tr in table.find_all("tr"):
        pos = _txt(tr.select_one(".position"))
        link = tr.find("a")
        val = _txt(tr.select_one(".points"))
        if not link or not val:
            continue
        slug = (link.get("href") or "").split("?")[0].rstrip("/").rsplit("/", 1)[-1]
        try:
            value = float(val) if "." in val else int(val)
        except ValueError:
            continue
        out.append(
            {
                "rank": int(pos) if pos.isdigit() else None,
                "player": re.sub(r"\s+", " ", link.get_text(" ")).strip().upper(),
                "player_slug": slug,
                "value": value,
            }
        )
    return out


LOGO_RE = re.compile(r"/uploads/images/logo/(?P<comp>\d+)/[hv]/\d+/(?P<id>\d+)\.png")


def parse_clubs(html: str) -> list[dict]:
    """Daftar klub + logo dari strip logo di beranda kompetisi.

    <a href="http://www.aremafc.com"><img src=".../logo/95/h/50/32.png" title="AREMA FC"></a>
    """
    soup = _soup(html)
    out, seen = [], set()
    for img in soup.find_all("img"):
        m = LOGO_RE.search(img.get("src") or "")
        name = clean_team(img.get("title") or (img.get("alt") or "").removeprefix("logo-"))
        if not m or not name or name in seen:
            continue
        seen.add(name)
        a = img.find_parent("a")
        site = (a.get("href") or "").strip() if a else ""
        out.append({"team": name, "club_id": m["id"], "website": site if re.match(r"https?://[\w.-]+\.\w+", site) else None})
    return out


def logo_urls(club_id: str, comp_id: str = "95") -> dict[str, str]:
    base = f"https://assets.ileague.id/uploads/images/logo/{comp_id}"
    return {
        "logo_url_small": f"{base}/h/50/{club_id}.png",
        "logo_url_large": f"{base}/v/150/{club_id}.png",
    }


def parse_club_options(html: str) -> dict[str, str]:
    """Ambil {club_id: nama_klub} dari dropdown klub di halaman leaderboard."""
    soup = _soup(html)
    sel = soup.select_one("select[name=klub]")
    clubs: dict[str, str] = {}
    if sel is None:
        return clubs
    for opt in sel.find_all("option"):
        v = (opt.get("value") or "").strip()
        name = clean_team(opt.get_text())
        if v and not name.startswith("-") and name != "SEMUA":
            clubs[v] = name
    return clubs
