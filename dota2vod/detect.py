"""Classify frames as in-game or not by OCRing the Dota 2 spectator top bar.

During a game the spectator HUD always shows, centered at the very top of the
frame: <team name> <kill score>  <game clock>  <kill score> <team name>.
We crop that strip, OCR it, and call a frame in-game when we find a clock
(mm:ss) flanked by at least one kill-score digit. The flanking words give us
the team names for free.
"""

from __future__ import annotations

import io
import os
import re
import subprocess
from dataclasses import dataclass, field

from PIL import Image, ImageOps

CLOCK_RE = re.compile(r"^-?\d{1,3}[:.]\d{2}$")
SCORE_RE = re.compile(r"^\d{1,2}$")
# Tokens that show up in the top bar but are never team names.
JUNK_TOKENS = {"VS", "V", "AM", "PM", "DAY", "NIGHT"}

# Fraction of the frame occupied by the crop: full-width top strip would pick
# up corner overlays (tournament logos, tickers), so we keep the center only.
STRIP_HEIGHT_FRAC = 0.085
STRIP_WIDTH_FRAC = 0.40
OCR_SCALE = 3
MIN_WORD_CONF = 35.0


@dataclass
class Word:
    text: str
    left: int
    top: int
    width: int
    conf: float

    @property
    def center_x(self) -> float:
        return self.left + self.width / 2


@dataclass
class FrameClass:
    in_game: bool
    clock: str | None = None
    left_team: str = ""
    right_team: str = ""
    words: list[Word] = field(default_factory=list)


def crop_strip(img: Image.Image) -> Image.Image:
    w, h = img.size
    x0 = int(w * (0.5 - STRIP_WIDTH_FRAC / 2))
    x1 = int(w * (0.5 + STRIP_WIDTH_FRAC / 2))
    return img.crop((x0, 0, x1, int(h * STRIP_HEIGHT_FRAC)))


def _prep(strip: Image.Image) -> Image.Image:
    g = strip.convert("L")
    g = g.resize((g.width * OCR_SCALE, g.height * OCR_SCALE), Image.LANCZOS)
    return ImageOps.autocontrast(g)


def _ocr_words(img: Image.Image, psm: int) -> list[Word]:
    buf = io.BytesIO()
    img.save(buf, "PNG")
    # OMP_THREAD_LIMIT=1: tesseract's OpenMP threads thrash badly when several
    # OCR processes run in parallel; single-threaded instances are far faster.
    env = {**os.environ, "OMP_THREAD_LIMIT": "1"}
    try:
        out = subprocess.run(
            ["tesseract", "stdin", "stdout", "--psm", str(psm), "tsv"],
            input=buf.getvalue(),
            capture_output=True,
            timeout=60,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return []
    if out.returncode != 0:
        return []
    words: list[Word] = []
    for line in out.stdout.decode("utf-8", "replace").splitlines()[1:]:
        cols = line.split("\t")
        if len(cols) < 12 or cols[0] != "5":
            continue
        text = cols[11].strip()
        if not text:
            continue
        try:
            words.append(
                Word(
                    text=text,
                    left=int(cols[6]),
                    top=int(cols[7]),
                    width=int(cols[8]),
                    conf=float(cols[10]),
                )
            )
        except ValueError:
            continue
    return words


def _find_clock(words: list[Word]) -> Word | None:
    for w in words:
        if w.conf >= MIN_WORD_CONF and CLOCK_RE.match(w.text.strip(".,")):
            return w
    return None


def _team_name(words: list[Word]) -> str:
    parts = []
    for w in words:
        token = re.sub(r"[^0-9A-Za-z]", "", w.text).upper()
        if len(token) < 2 or not re.search(r"[A-Z]", token):
            continue
        if token in JUNK_TOKENS or w.conf < MIN_WORD_CONF:
            continue
        parts.append(token)
    return " ".join(parts)


def classify_frame(img: Image.Image, lenient: bool = False) -> FrameClass:
    """Decide whether the Dota in-game HUD is on screen and read the team names."""
    strip = _prep(crop_strip(img))
    words = _ocr_words(strip, psm=7)
    clock = _find_clock(words)
    if clock is None:
        words = _ocr_words(strip, psm=6)
        clock = _find_clock(words)
    if clock is None:
        return FrameClass(in_game=False, words=words)

    left = [w for w in words if w.center_x < clock.left]
    right = [w for w in words if w.center_x > clock.left + clock.width]
    has_score = any(SCORE_RE.match(w.text) for w in left) or any(
        SCORE_RE.match(w.text) for w in right
    )
    in_game = has_score or lenient
    return FrameClass(
        in_game=in_game,
        clock=clock.text,
        left_team=_team_name(left),
        right_team=_team_name(right),
        words=words,
    )
