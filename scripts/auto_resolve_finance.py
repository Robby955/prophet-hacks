#!/usr/bin/env python3
"""Auto-resolve the financial and crypto shadow-calibration events.

Reads the queued finance/crypto events from
``data/shadow_calibration/events.json``, fetches the deciding market data from
keyless public sources, computes the binary Yes/No winner, and merges the
result into ``data/shadow_calibration/resolutions.json``.

It only touches events it can resolve mechanically (equity/ETF closes and
crypto spot thresholds). Sports, macro releases, and anything it cannot fetch
are left untouched so you can fill them in by hand. By default it never
overwrites a resolution that already exists — pass ``--force`` for that.

Data sources (no API key required, fetched via curl so it works behind a
TLS-intercepting proxy):
  * Equities / ETFs: Yahoo Finance chart API (query1.finance.yahoo.com)
  * Crypto:          Coinbase Exchange hourly candles + spot fallback

Event-ticker grammar this understands:
  SHADOW-FIN-<SYM>-CLOSE-ABOVE-<THRESH>-<YYYYMMDD>   close strictly above threshold
  SHADOW-FIN-<SYM>-NEXT-DAY-UP-<YYYYMMDD>            close above prior trading-day close
  SHADOW-FIN-<SYM>-UP-AFTER-...-<YYYYMMDD>           close above prior trading-day close
  SHADOW-CRYPTO-<SYM>-ABOVE-<THRESH>-<YYYYMMDD>      price strictly above threshold at close_time
  SHADOW-CRYPTO-<SYM>-GE-<THRESH>-<YYYYMMDD>         price at or above threshold at close_time

Usage:
  python3 scripts/auto_resolve_finance.py            # resolve due events, write file
  python3 scripts/auto_resolve_finance.py --dry-run  # show what it would write
  python3 scripts/auto_resolve_finance.py --force     # overwrite existing entries too
"""

from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

EVENTS = Path("data/shadow_calibration/events.json")
RESOLUTIONS = Path("data/shadow_calibration/resolutions.json")

YAHOO_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range=3mo&interval=1d"
COINBASE_CANDLES = (
    "https://api.exchange.coinbase.com/products/{product}/candles"
    "?granularity=3600&start={start}&end={end}"
)
COINBASE_SPOT = "https://api.coinbase.com/v2/prices/{product}/spot"
USER_AGENT = "Mozilla/5.0 (prophet-hacks-shadow-resolver/1.0)"


class FetchError(RuntimeError):
    pass


def _http_get(url: str, timeout: int = 20) -> str:
    """GET via curl. curl uses the system trust store, so it works behind the
    sandbox's TLS-intercepting proxy where Python's urllib bundle does not."""
    proc = subprocess.run(
        ["curl", "-s", "--fail", "--max-time", str(timeout), "-H", f"User-Agent: {USER_AGENT}", url],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise FetchError(f"curl exit {proc.returncode}: {proc.stderr.strip() or url}")
    return proc.stdout


# --------------------------------------------------------------------------
# ticker -> resolution spec
# --------------------------------------------------------------------------
def parse_spec(event: dict[str, Any]) -> dict[str, Any] | None:
    """Return a resolver spec derived from the event ticker, or None to skip."""
    ticker = str(event.get("event_ticker") or "")
    parts = ticker.split("-")
    if len(parts) < 4 or parts[0] != "SHADOW":
        return None
    domain = parts[1]
    if domain not in ("FIN", "CRYPTO"):
        return None
    symbol = parts[2]
    middle = parts[3:-1]  # tokens between the symbol and the trailing YYYYMMDD
    date_token = parts[-1]
    try:
        event_date = datetime.strptime(date_token, "%Y%m%d").date()
    except ValueError:
        return None

    numeric = [float(t) for t in middle if t.replace(".", "", 1).isdigit()]
    threshold = numeric[0] if numeric else None
    hi = numeric[1] if len(numeric) > 1 else None

    if "BETWEEN" in middle and hi is not None:
        kind = "between"  # lo <= px <= hi (narrow-interval shape PA uses)
    elif "UP" in middle and threshold is None:
        kind = "next_day_up"
    elif "GE" in middle:
        kind = "ge"  # >=
    elif "BELOW" in middle:
        kind = "lt"  # <
    elif "ABOVE" in middle:
        kind = "gt"  # >
    else:
        return None

    return {
        "ticker": ticker,
        "asset": "crypto" if domain == "CRYPTO" else "equity",
        "symbol": symbol,
        "kind": kind,
        "threshold": threshold,
        "hi": hi,
        "event_date": event_date,
        "close_time": event.get("close_time"),
    }


def _winner_for(kind: str, px: float, thr: float, hi: float | None = None) -> str:
    if kind == "ge":
        return "Yes" if px >= thr else "No"
    if kind == "lt":
        return "Yes" if px < thr else "No"
    if kind == "between":
        return "Yes" if (thr <= px <= hi) else "No"
    return "Yes" if px > thr else "No"  # gt


def _thr_desc(kind: str, thr: float, hi: float | None = None) -> str:
    return {"ge": f">= {thr}", "lt": f"< {thr}", "between": f"in [{thr}, {hi}]"}.get(kind, f"> {thr}")


# --------------------------------------------------------------------------
# data fetchers
# --------------------------------------------------------------------------
def fetch_equity_closes(symbol: str) -> dict[str, float]:
    """Return {YYYY-MM-DD: regular-session close} for the last ~3 months."""
    data = json.loads(_http_get(YAHOO_URL.format(sym=symbol.upper())))
    result = data["chart"]["result"][0]
    stamps = result["timestamp"]
    closes = result["indicators"]["quote"][0]["close"]
    out: dict[str, float] = {}
    for stamp, close in zip(stamps, closes):
        if close is None:
            continue
        day = datetime.fromtimestamp(stamp, timezone.utc).strftime("%Y-%m-%d")
        out[day] = float(close)
    return out


def fetch_crypto_price(symbol: str, close_time: str | None) -> tuple[float | None, str]:
    """Return (price_at_close_time, source_note). Falls back to live spot."""
    product = f"{symbol}-USD"
    if close_time:
        try:
            target = datetime.fromisoformat(close_time.replace("Z", "+00:00"))
            target = target.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
            start = target.strftime("%Y-%m-%dT%H:%M:%SZ")
            end = (target + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
            url = COINBASE_CANDLES.format(product=product, start=start, end=end)
            candles = json.loads(_http_get(url))  # [[time, low, high, open, close, vol], ...]
            target_unix = int(target.timestamp())
            for candle in candles:
                if int(candle[0]) == target_unix:
                    return float(candle[3]), f"coinbase candle open @ {start}"
            if candles:
                nearest = min(candles, key=lambda c: abs(int(c[0]) - target_unix))
                when = datetime.fromtimestamp(int(nearest[0]), timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
                return float(nearest[3]), f"coinbase nearest candle open ({when})"
        except (FetchError, ValueError, KeyError, IndexError):
            pass
    # fallback: live spot (only point-in-time-correct if close_time is ~now)
    try:
        data = json.loads(_http_get(COINBASE_SPOT.format(product=product)))
        return float(data["data"]["amount"]), "coinbase spot (fallback, not point-in-time)"
    except (FetchError, ValueError, KeyError):
        return None, "no data"


# --------------------------------------------------------------------------
# resolution
# --------------------------------------------------------------------------
def resolve_one(spec: dict[str, Any]) -> dict[str, Any] | None:
    """Return a resolution record, {"_error": ...} on a hard error, or None
    if the event simply is not resolvable yet (no data for the date)."""
    kind = spec["kind"]
    iso_date = spec["event_date"].strftime("%Y-%m-%d")

    if spec["asset"] == "equity":
        try:
            closes = fetch_equity_closes(spec["symbol"])
        except (FetchError, ValueError, KeyError, IndexError) as exc:
            return {"_error": f"fetch failed: {exc}"}
        if iso_date not in closes:
            return None  # session not closed / no data yet
        close_px = closes[iso_date]
        if kind == "next_day_up":
            prior = [d for d in sorted(closes) if d < iso_date]
            if not prior:
                return {"_error": "no prior trading-day close available"}
            prev_px = closes[prior[-1]]
            winner = "Yes" if close_px > prev_px else "No"
            note = f"{spec['symbol']} close {close_px} vs prior {prev_px} ({prior[-1]})"
            observed = close_px
        else:  # threshold comparison (gt / ge / lt / between)
            winner = _winner_for(kind, close_px, spec["threshold"], spec.get("hi"))
            note = f"{spec['symbol']} close {close_px} vs {_thr_desc(kind, spec['threshold'], spec.get('hi'))}"
            observed = close_px
        source = "yahoo finance chart API (daily close)"

    else:  # crypto
        price, src_note = fetch_crypto_price(spec["symbol"], spec["close_time"])
        if price is None:
            return None
        winner = _winner_for(kind, price, spec["threshold"], spec.get("hi"))
        note = f"{spec['symbol']} {price} vs {_thr_desc(kind, spec['threshold'], spec.get('hi'))} ({src_note})"
        observed = price
        source = src_note

    return {
        "winner": winner,
        "resolved_at": spec["close_time"] or f"{iso_date}T20:00:00Z",
        "observed": observed,
        "source": source,
        "notes": f"auto-resolved: {note}",
        "auto_resolved_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--dry-run", action="store_true", help="compute but do not write the file")
    ap.add_argument("--force", action="store_true", help="overwrite resolutions that already exist")
    ap.add_argument("--events", type=Path, default=EVENTS)
    ap.add_argument("--resolutions", type=Path, default=RESOLUTIONS)
    args = ap.parse_args(argv)

    events = json.loads(args.events.read_text())
    existing: dict[str, Any] = {}
    if args.resolutions.exists():
        existing = json.loads(args.resolutions.read_text())

    now = datetime.now(timezone.utc)
    resolved: dict[str, Any] = {}
    skipped: list[tuple[str, str]] = []
    pending: list[tuple[str, str]] = []

    for event in events:
        spec = parse_spec(event)
        if spec is None:
            continue
        ticker = spec["ticker"]

        if ticker in existing and not args.force:
            skipped.append((ticker, "already resolved"))
            continue

        close_time = spec.get("close_time")
        if close_time:
            try:
                ct = datetime.fromisoformat(close_time.replace("Z", "+00:00"))
                if ct > now:
                    pending.append((ticker, f"closes {close_time} (not yet)"))
                    continue
            except ValueError:
                pass

        record = resolve_one(spec)
        if record is None:
            pending.append((ticker, "no market data for date yet"))
            continue
        if "_error" in record:
            skipped.append((ticker, record["_error"]))
            continue
        resolved[ticker] = record

    # report
    print(f"resolvable finance/crypto events scanned at {now.isoformat()}\n")
    for ticker, rec in sorted(resolved.items()):
        print(f"  RESOLVED  {ticker}  -> {rec['winner']:<3}  ({rec['notes']})")
    for ticker, why in sorted(pending):
        print(f"  pending   {ticker}  ({why})")
    for ticker, why in sorted(skipped):
        print(f"  skipped   {ticker}  ({why})")
    print(f"\nnewly resolved: {len(resolved)}  pending: {len(pending)}  skipped: {len(skipped)}")

    if not resolved:
        print("nothing to write.")
        return 0
    if args.dry_run:
        print("\n--dry-run: not writing. Records that would be added:")
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
