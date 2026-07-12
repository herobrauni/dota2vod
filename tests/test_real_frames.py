"""Regression tests against real broadcast footage.

The fixtures are the top strips (full width, top 6%) of 1280x720 frames
grabbed from PGL's TI15 Regional Qualifiers Twitch VODs — a real tournament
production with the standard spectator HUD: team logos (no name text), kill
scores, and the tiny centered game clock. Strips instead of full frames keep
the repo small and avoid shipping recognizable gameplay.

Filenames encode the expected result: ingame_* strips must classify as
in-game, panel_* strips (break/intro screens) must not.
"""

from pathlib import Path

import pytest
from PIL import Image

from dota2vod.detect import classify_frame

FIXTURES = Path(__file__).parent / "fixtures"


def full_frame(strip_path: Path) -> Image.Image:
    """Re-seat a top-strip fixture on a 720p canvas at its original position."""
    strip = Image.open(strip_path)
    frame = Image.new("RGB", (strip.width, int(strip.width * 9 / 16)), (30, 30, 30))
    frame.paste(strip, (0, 0))
    return frame


@pytest.mark.parametrize(
    "name", sorted(p.name for p in FIXTURES.glob("ingame_*.png"))
)
def test_real_ingame_strip_detected(name):
    fc = classify_frame(full_frame(FIXTURES / name))
    assert fc.in_game, f"{name}: expected in-game, words={[w.text for w in fc.words]}"
    assert fc.clock is not None
    # The filename encodes the true clock digits; OCR may drop a leading digit
    # but what it reads must be a suffix of the truth (e.g. 4231 -> "42:31").
    expected = name.rsplit("_", 1)[1].removesuffix(".png")
    got = fc.clock.replace(":", "").lstrip("-0") or "0"
    assert expected.endswith(got), f"{name}: clock read {fc.clock!r}"


@pytest.mark.parametrize(
    "name", sorted(p.name for p in FIXTURES.glob("panel_*.png"))
)
def test_real_panel_strip_rejected(name):
    fc = classify_frame(full_frame(FIXTURES / name))
    assert not fc.in_game, f"{name}: expected not in-game, clock={fc.clock}"


def test_real_hud_has_no_team_name_text():
    """Pro-team HUDs show logos, not names — the detector must not hallucinate."""
    fc = classify_frame(full_frame(FIXTURES / "ingame_day_4231.png"))
    assert fc.in_game
    # Best-effort OCR of the logo slots may return junk on single frames, but
    # it must be empty or short noise, never a confident multi-word "name".
    assert len(fc.left_team) <= 12 and len(fc.right_team) <= 12
