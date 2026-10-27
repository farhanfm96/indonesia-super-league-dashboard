"""HTTP layer for ileague.id: one shared session, polite delays, retries."""
from __future__ import annotations

import time

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

BASE_URL = "https://ileague.id"
COMPETITION = "BRI_SUPER_LEAGUE_2026-27"

# Jeda antar request (detik). Situs ini milik operator liga, jangan dibanjiri.
REQUEST_DELAY = 1.0

_session: requests.Session | None = None
_last_request = 0.0


def session() -> requests.Session:
    global _session
    if _session is None:
        s = requests.Session()
        s.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (compatible; isl-dashboard-portfolio/1.0; "
                    "+https://github.com/)"
                ),
                "Accept-Language": "id-ID,id;q=0.9,en;q=0.8",
            }
        )
        retry = Retry(
            total=4,
            backoff_factor=2,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=("GET", "POST"),
        )
        s.mount("https://", HTTPAdapter(max_retries=retry))
        _session = s
    return _session


def _throttle() -> None:
    global _last_request
    wait = REQUEST_DELAY - (time.monotonic() - _last_request)
    if wait > 0:
        time.sleep(wait)
    _last_request = time.monotonic()


def get(path: str) -> str:
    _throttle()
    url = path if path.startswith("http") else BASE_URL + path
    r = session().get(url, timeout=30)
    r.raise_for_status()
    return r.text


def post(path: str, data: dict) -> str:
    _throttle()
    url = path if path.startswith("http") else BASE_URL + path
    r = session().post(url, data=data, timeout=30)
    r.raise_for_status()
    return r.text


# ---- URL builders ---------------------------------------------------------

def fixtures_path(week: int) -> str:
    return f"/fixtures/index/{COMPETITION}/{week}/0"


def leaderboard_path() -> str:
    return f"/top_actions/player/{COMPETITION}"
