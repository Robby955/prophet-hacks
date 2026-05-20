"""Auto-resolve the sports shadow-calibration events from the keyless ESPN
scoreboard API.

Reads SHADOW-{MLB,NBA,NHL,WNBA,SOCCER} events from
``data/shadow_calibration/events.json``, finds each game on ESPN by matching the
two competitor names against the event's outcome labels (full team displayNames,
which ESPN returns verbatim), reads the final winner, and merges it into
``data/shadow_calibration/resolutions.json`` without clobbering manual rows.

Games that are not final yet, or can't be matched confidently, are left for you
to fill by hand. By default never overwrites an existing resolution — use
``--force``.

Data source: ESPN site API (no key), fetched via curl so it works behind the
sandbox's TLS-intercepting proxy.

Ticker grammar: ``SHADOW-<LEAGUE>-<...>-<YYYYMMDD>`` (e.g. SHADOW-MLB-ATL-MIA-20260519).
The trailing date is the US game date; we also probe +/-1 day for timezone slop.

Usage:
    python scripts/auto_resolve_sports.py --dry-run
    python scripts/auto_resolve_sports.py
    python scripts/auto_resolve_sports.py --force --dry-run   # re-check resolved rows
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

EVENTS = Path("data/shadow_calibration/events.json")
RESOLUTIONS = Path("data/shadow_calibration/resolutions.json")

ESPN = "https://site.api.espn.com/apis/site/v2/sports/{path}/scoreboard?dates={date}"
LEAGUE_PATHS = {
    "MLB": "baseball/mlb",
    "NBA": "basketball/nba",
    "NHL": "hockey/nhl",
    "WNBA": "basketball/wnba",
}
# Soccer needs a league slug; probe the common ones for our queued events.
SOCCER_PATHS = ["soccer/uefa.europa", "soccer/uefa.champions", "soccer/eng.1", "soccer/usa.1"]
UA = "Mozilla/5.0 (prophet-hacks-shadow-resolver/1.0)"


class FetchError(RuntimeError):
    pass


def _http_get(url: str, timeout: int = 20) -> str:
    proc = subprocess.run(
        ["curl", "-s", "--fail", "--max-time", str(timeout), "-H", f"User-Agent: {UA}", url],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise FetchError(f"curl exit {proc.returncode}: {proc.stderr.strip() or url}")
    return proc.stdout


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def parse_spec(event: dict) -> dict | None:
    ticker = str(event.get("event_ticker") or "")
    parts = ticker.split("-")
    if len(parts) < 4 or parts[0] != "SHADOW":
        return None
    league = parts[1]
    if league not in LEAGUE_PATHS and league != "SOCCER":
        return None
    try:
        date = datetime.strptime(parts[-1], "%Y%m%d").date()
    except ValueError:
        return None
    outcomes = [str(o) for o in (event.get("outcomes") or [])]
    if len(outcomes) < 2:
        return None
    return {"ticker": ticker, "league": league, "date": date,
            "outcomes": outcomes, "close_time": event.get("close_time")}


def _games_from_payload(text: str) -> list[dict]:
    """Normalize an ESPN scoreboard payload into simple game dicts."""
    data = json.loads(text)
    games = []
    for ev in data.get("events", []) or []:
        for comp in ev.get("competitions", []) or []:
            status = (((comp.get("status") or {}).get("type") or {}).get("name")) or ""
            teams = []
            for c in comp.get("competitors", []) or []:
                t = c.get("team") or {}
                teams.append({
                    "name": t.get("displayName") or t.get("name") or "",
                    "abbr": t.get("abbreviation") or "",
                    "score": c.get("score"),
                    "winner": bool(c.get("winner")),
                })
            games.append({"status": status, "teams": teams})
    return games


def fetch_scoreboard(league: str, date) -> list[dict]:
    """Fetch games for a date, probing +/-1 day for timezone slop."""
    paths = [LEAGUE_PATHS[league]] if league in LEAGUE_PATHS else SOCCER_PATHS
    out: list[dict] = []
    for d in (date, date - timedelta(days=1), date + timedelta(days=1)):
        ds = d.strftime("%Y%m%d")
        for path in paths:
            try:
                out.extend(_games_from_payload(_http_get(ESPN.format(path=path, date=ds))))
            except (FetchError, ValueError):
                continue
    return out


def match_game(games: list[dict], outcomes: list[str]) -> dict | None:
    """Find the game whose two competitors match the event's outcome labels."""
    want = {_norm(o) for o in outcomes}
    abbr_want = None  # built lazily if name match fails
    for g in games:
        names = {_norm(t["name"]) for t in g["teams"]}
        if want and want.issubset(names):
            return g
    # fallback: match on abbreviations if outcomes look like they contain them
    for g in games:
        abbrs = {_norm(t["abbr"]) for t in g["teams"] if t["abbr"]}
        if abbr_want is None:
            abbr_want = {_norm(o) for o in outcomes}
        if abbrs and abbr_want.issubset(abbrs):
            return g
    return None


def resolve_one(spec: dict) -> dict | None:
    try:
        games = fetch_scoreboard(spec["league"], spec["date"])
    except (FetchError, ValueError) as exc:
        return {"_error": f"fetch failed: {exc}"}
    if not games:
        return None
    game = match_game(games, spec["outcomes"])
    if game is None:
        return None  # not found yet (schedule slop / not posted)
    if "FINAL" not in (game["status"] or "").upper():
        return None  # not final yet
    winners = [t for t in game["teams"] if t["winner"]]
    if len(winners) != 1:
        return {"_error": f"ambiguous winner (status {game['status']}, draw?)"}
    win_name = winners[0]["name"]
    # map ESPN winner back to the EXACT outcome label the scorer expects
    label = next((o for o in spec["outcomes"] if _norm(o) == _norm(win_name)), None)
    if label is None:
        label = next((o for o in spec["outcomes"]
                      if _norm(o) == _norm(winners[0]["abbr"])), None)
    if label is None:
        return {"_error": f"winner '{win_name}' did not map to an outcome label"}
    score = " - ".join(f"{t['name']} {t['score']}" for t in game["teams"])
    return {
        "winner": label,
        "resolved_at": spec["close_time"] or f"{spec['date'].isoformat()}T23:59:00Z",
        "source": "ESPN scoreboard API",
        "notes": f"auto-resolved: final {score}",
        "auto_resolved_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true", help="re-resolve rows that already exist")
    ap.add_argument("--events", type=Path, default=EVENTS)
    ap.add_argument("--resolutions", type=Path, default=RESOLUTIONS)
    args = ap.parse_args()

    events = json.loads(args.events.read_text())
    existing = json.loads(args.resolutions.read_text()) if args.resolutions.exists() else {}
    now = datetime.now(timezone.utc)

    resolved, pending, skipped = {}, [], []
    for event in events:
        spec = parse_spec(event)
        if spec is None:
            continue
        ticker = spec["ticker"]
        if ticker in existing and not args.force:
            skipped.append((ticker, "already resolved"))
            continue
        ct = spec.get("close_time")
        if ct:
            try:
                if datetime.fromisoformat(ct.replace("Z", "+00:00")) > now:
                    pending.append((ticker, f"starts {ct} (not played)"))
                    continue
            except ValueError:
                pass
        rec = resolve_one(spec)
        if rec is None:
            pending.append((ticker, "no final result yet"))
        elif "_error" in rec:
            skipped.append((ticker, rec["_error"]))
        else:
            resolved[ticker] = rec

    print(f"sports scan at {now.isoformat()}\n")
    for t, r in sorted(resolved.items()):
        print(f"  RESOLVED  {t}  -> {r['winner']}  ({r['notes']})")
    for t, why in sorted(pending):
        print(f"  pending   {t}  ({why})")
    for t, why in sorted(skipped):
        print(f"  skipped   {t}  ({why})")
    print(f"\nnewly resolved: {len(resolved)}  pending: {len(pending)}  skipped: {len(skipped)}")

    if not resolved:
        print("nothing to write.")
        return 0
    if args.dry_run:
        print("\n--dry-run: not writing.")
        print(json.dumps(resolved, indent=2, sort_keys=True))
        return 0
    merged = dict(existing)
    merged.update(resolved)
    args.resolutions.write_text(json.dumps(merged, indent=2, sort_keys=True) + "\n")
    print(f"\nwrote {len(resolved)} resolution(s) to {args.resolutions}")
    print("next: python3 scripts/score_shadow_calibration.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
