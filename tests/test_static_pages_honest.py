"""Regression guard: keep the public static pages on the honest headline.

We deliberately replaced a leakage-inflated headline Brier (the stale precise
values 0.0379 / 0.0378) with the honest, date-disciplined 0.118 across every
public page. These tests make that swap stick and stop ASCII-only prose from
regressing to decorative non-ASCII punctuation.

status.html is auth-gated (PIN), so it is not a public page and is skipped.
"""

import re
from pathlib import Path


# Every public static HTML page (status.html is auth-gated, so excluded).
PUBLIC_PAGES = sorted(
    p for p in Path("static").glob("*.html") if p.name != "status.html"
)

# Pages that actually report the headline result; they must carry the honest
# figure (0.118) or an explicit leakage note so the framing is present.
RESULT_PAGES = {
    "summary.html",
    "variance.html",
    "overview.html",
    "diagnostics.html",
    "bootstrap_hist.html",
    "gallery_resolved.html",
}

# Always-stale precise replay values. 0.038 is allowed when LABELED as
# hindsight, but the 4-decimal forms are only ever the stale headline.
STALE_HEADLINE_VALUES = ["0.0379", "0.0378"]

# Non-ASCII punctuation that must not appear in page chrome/prose. Accented
# letters in data are fine, so we strip data regions before checking (see
# _strip_data_regions). The minus sign and middot are intentionally NOT here:
# they are used pervasively as legitimate typographic content.
BANNED_PUNCTUATION = {
    "—": "em dash",
    "–": "en dash",
    "×": "multiplication sign",
    "→": "right arrow",
    "←": "left arrow",
    "↑": "up arrow",
    "↓": "down arrow",
    "Δ": "capital delta",
    "≤": "less-than-or-equal",
    "≥": "greater-than-or-equal",
}

_JSON_BLOCK = re.compile(
    r'<script[^>]*type="application/json"[^>]*>.*?</script>',
    re.DOTALL,
)
_TITLE_ATTR = re.compile(r'title="[^"]*"')


def _strip_data_regions(text: str) -> str:
    """Drop data-bearing regions so the punctuation check sees only chrome.

    Embedded JSON payloads and per-cell ``title="..."`` tooltips carry real
    event data (accented names, reasoning blobs). The task allows accented
    letters in data, so those regions are removed before scanning.
    """

    text = _JSON_BLOCK.sub("", text)
    text = _TITLE_ATTR.sub('title=""', text)
    return text


def test_public_pages_exist() -> None:
    # Guard against the glob silently matching nothing (e.g. wrong cwd).
    assert PUBLIC_PAGES, "expected at least one public static page"
    names = {p.name for p in PUBLIC_PAGES}
    assert "status.html" not in names
    assert "summary.html" in names


def test_no_stale_headline_brier() -> None:
    for path in PUBLIC_PAGES:
        text = path.read_text()
        for value in STALE_HEADLINE_VALUES:
            # Reject the value only as a bare token (a standalone number), not
            # as a substring of a longer, explicitly-labeled hindsight float
            # such as 0.03782 / 0.037889.
            pattern = re.compile(r"(?<![\d.])" + re.escape(value) + r"(?![\d])")
            assert not pattern.search(text), (
                f"{path} still shows the stale headline value {value!r}"
            )


def test_result_pages_carry_honest_framing() -> None:
    for path in PUBLIC_PAGES:
        if path.name not in RESULT_PAGES:
            continue
        text = path.read_text()
        has_honest_number = "0.118" in text
        has_leakage_note = "leakage" in text.lower()
        assert has_honest_number or has_leakage_note, (
            f"{path} must contain the honest figure 0.118 or a leakage note"
        )


def test_pages_avoid_banned_non_ascii_punctuation() -> None:
    for path in PUBLIC_PAGES:
        chrome = _strip_data_regions(path.read_text())
        for char, label in BANNED_PUNCTUATION.items():
            assert char not in chrome, f"{path} still contains a {label}"
