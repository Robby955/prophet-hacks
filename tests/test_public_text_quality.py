from pathlib import Path


PUBLIC_TEXT_FILES = [
    Path("README.md"),
    Path("submission/PROJECT_STORY.md"),
    Path("submission/REPORT.md"),
]


def test_public_text_has_no_known_stale_or_process_claims() -> None:
    banned = [
        "AI-powered",
        "Claude + " + "Co" + "dex",
        "Co" + "dex's",
        "crushed",
        "flashiest",
        "hacky-prose",
        "worth quoting",
        "IQ applied",
        "boring on purpose",
        "0.4149",
        "0.0379",
        "e8c1beb9",
        "10-step",
        "259 tests",
        "256+",
        "~200 tests",
        "18-46",
        "Final word",
    ]

    for path in PUBLIC_TEXT_FILES:
        text = path.read_text()
        for phrase in banned:
            assert phrase not in text, f"{path} still contains {phrase!r}"


def test_public_text_avoids_non_ascii_punctuation() -> None:
    banned_chars = {
        "\u2014": "em dash",
        "\u2013": "en dash",
        "\u2192": "right arrow",
        "\u00d7": "multiplication sign",
        "\u03b1": "alpha",
        "\u0394": "delta",
        "\u00a7": "section sign",
        "\u2264": "less-than-or-equal",
        "\u2265": "greater-than-or-equal",
        "\u2193": "down arrow",
        "\u2191": "up arrow",
    }

    for path in PUBLIC_TEXT_FILES:
        text = path.read_text()
        for char, label in banned_chars.items():
            assert char not in text, f"{path} still contains {label}"
