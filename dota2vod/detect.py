"""Classify frames as in-game or not by OCRing the Dota 2 spectator top bar.

During a game the spectator HUD shows, centered at the very top of the frame:

    [team logo] [portraits] [kill score]  [game clock]  [kill score] [portraits] [logo]

The game clock (mm:ss, possibly negative pre-horn) sits in a small box at the
horizontal center, with the two kill scores in fixed slots just outside it.
We OCR each of those boxes separately: tight crops are what makes the tiny
HUD text readable, OCRing the whole strip drowns the clock in hero-portrait
noise (verified against real PGL broadcast VODs at 720p).

Team names are usually NOT text in the top bar — pro teams show logos there.
Valve's HUD only falls back to a text tag when a team has no logo set, so we
OCR the logo slots as a best effort and return "" when nothing readable is
found (the common case; logos OCR as sporadic junk, which the majority vote
in segments.pick_team_names filters out).
"""

from __future__ import annotations

import io
import os
import re
import subprocess
from dataclasses import dataclass, field

from PIL import Image, ImageOps

CLOCK_RE = re.compile(r"^-?\d{1,3}[:.]\d{2}$")
# Clock with the ':' dropped by OCR (it is ~2px wide at 720p): digits whose
# last two form valid seconds, e.g. "4231" -> 42:31.
CLOCK_NO_COLON_RE = re.compile(r"^-?\d{3,5}$")
SCORE_RE = re.compile(r"^\d{1,3}$")

# HUD regions as frame-size fractions (x0, x1, y0, y1), measured on real
# 1280x720 broadcast footage; the HUD scales with resolution so fractions
# hold at other sizes. The clock box must stop short of the kill-score
# digits (they end/start at ~0.475 / ~0.525) or tesseract merges everything
# into one garbled line.
CLOCK_BOX = (0.480, 0.520, 0.015, 0.040)
SCORE_BOXES = ((0.450, 0.4745, 0.010, 0.038), (0.5255, 0.550, 0.010, 0.038))
# Logo / team-tag slots just outside the hero portraits.
NAME_BOXES = ((0.185, 0.285, 0.0, 0.05), (0.715, 0.815, 0.0, 0.05))
# Player list area on the left side where team tags appear (e.g., "XG. PlayerName")
PLAYER_LIST_BOX = (0.0, 0.15, 0.08, 0.35)

OCR_SCALE = 4
MIN_CLOCK_CONF = 40.0
MIN_SCORE_CONF = 40.0
MIN_NAME_CONF = 60.0

# Preprocessing passes tried in order until one yields a clock: autocontrast
# reads most frames; the binarization thresholds recover dim night-time and
# busy-background frames (together 44/44 on the real-VOD eval set).
CLOCK_PASSES = ("auto", "bin170", "bin190")
# psm 7 (line) reads multi-digit scores; psm 10 (single char) recovers the
# lone "0"/"1" of an early game that line segmentation refuses to see.
SCORE_PASSES = (("bin170", 7), ("auto", 7), ("bin170", 10), ("auto", 10))

# Tokens that show up around the top bar but are never team names.
JUNK_TOKENS = {"VS", "V", "AM", "PM", "DAY", "NIGHT"}

# Map team tags to full team names (common abbreviations)
# These are extracted from player list prefixes like "XG. PlayerName"
TEAM_TAG_MAP = {
    "XG": "Xtreme Gaming",
    "TL": "Team Liquid",
    "SPIRIT": "Team Spirit",
    "NAVI": "NAVI",
    "GG": "Gaimin Gladiators",
    "FC": "Team Falcons",
    "LGD": "LGD Gaming",
    "TUNDRA": "Tundra",
    "BB": "Team Bold",
    "SR": "Sandrock",
    "AZ": "Azure",
}


@dataclass
class Word:
    text: str
    left: int
    top: int
    width: int
    conf: float


@dataclass
class FrameClass:
    in_game: bool
    clock: str | None = None
    left_team: str = ""
    right_team: str = ""
    words: list[Word] = field(default_factory=list)


def crop_box(img: Image.Image, box: tuple[float, float, float, float]) -> Image.Image:
    w, h = img.size
    x0, x1, y0, y1 = box
    return img.crop((int(w * x0), int(h * y0), int(w * x1), int(h * y1)))


def _prep(crop: Image.Image, mode: str = "auto") -> Image.Image:
    g = crop.convert("L")
    g = g.resize((g.width * OCR_SCALE, g.height * OCR_SCALE), Image.LANCZOS)
    if mode == "auto":
        return ImageOps.autocontrast(g)
    threshold = int(mode.removeprefix("bin"))
    return g.point(lambda p: 255 if p > threshold else 0)


def _ocr_words(img: Image.Image, psm: int, whitelist: str | None = None) -> list[Word]:
    buf = io.BytesIO()
    img.save(buf, "PNG")
    cmd = ["tesseract", "stdin", "stdout", "--psm", str(psm)]
    if whitelist:
        cmd += ["-c", f"tessedit_char_whitelist={whitelist}"]
    cmd += ["tsv"]
    # OMP_THREAD_LIMIT=1: tesseract's OpenMP threads thrash badly when several
    # OCR processes run in parallel; single-threaded instances are far faster.
    env = {**os.environ, "OMP_THREAD_LIMIT": "1"}
    try:
        out = subprocess.run(
            cmd,
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


def _parse_clock(words: list[Word]) -> str | None:
    for w in words:
        text = w.text.strip(".,")
        if w.conf < MIN_CLOCK_CONF:
            continue
        if CLOCK_RE.match(text):
            return text
        # ':' often drops out of the tiny clock glyphs; accept an all-digit
        # read when the trailing two digits are valid seconds.
        if CLOCK_NO_COLON_RE.match(text):
            digits = text.lstrip("-")
            if int(digits[-2:]) < 60:
                sign = "-" if text.startswith("-") else ""
                return f"{sign}{digits[:-2]}:{digits[-2:]}"
    return None


def _read_clock(img: Image.Image) -> tuple[str | None, list[Word]]:
    crop = crop_box(img, CLOCK_BOX)
    words: list[Word] = []
    for mode in CLOCK_PASSES:
        words = _ocr_words(_prep(crop, mode), psm=7, whitelist="0123456789:.-")
        clock = _parse_clock(words)
        if clock is not None:
            return clock, words
    return None, words


def _read_score(img: Image.Image, box: tuple[float, float, float, float]) -> bool:
    crop = crop_box(img, box)
    for mode, psm in SCORE_PASSES:
        # The margin matters (and must be >=20px): without it tesseract
        # silently drops a lone digit (the 0-0 score of an early game).
        prepped = ImageOps.expand(_prep(crop, mode), border=24, fill=0)
        for w in _ocr_words(prepped, psm=psm, whitelist="0123456789"):
            if w.conf >= MIN_SCORE_CONF and SCORE_RE.match(w.text):
                return True
    return False


def _read_team_name(img: Image.Image, box: tuple[float, float, float, float]) -> str:
    parts = []
    for w in _ocr_words(_prep(crop_box(img, box)), psm=7):
        token = re.sub(r"[^0-9A-Za-z]", "", w.text).upper()
        if len(token) < 2 or not re.search(r"[A-Z]", token):
            continue
        if token in JUNK_TOKENS or w.conf < MIN_NAME_CONF:
            continue
        parts.append(token)
    return " ".join(parts)


def _read_team_tag_from_players(img: Image.Image) -> str:
    """Extract team tag from player list (e.g., 'XG. PlayerName' -> 'XG')."""
    crop = crop_box(img, PLAYER_LIST_BOX)
    # Scale up more aggressively for small text
    g = crop.convert("L")
    g = g.resize((g.width * 6, g.height * 6), Image.LANCZOS)
    g = ImageOps.autocontrast(g)

    # Try multiple PSM modes to catch the list format
    for psm in (6, 3, 4):  # 6=uniform block, 3=column, 4=vertical line
        for w in _ocr_words(g, psm=psm):
            text = w.text.strip()
            # Look for patterns like "XG." or "XG_" at start of text
            match = re.match(r"^([A-Z]{2,5})[\._]", text)
            if match and w.conf >= 30:  # Lower threshold for scaled image
                tag = match.group(1)
                # Map to full team name if known
                return TEAM_TAG_MAP.get(tag.upper(), tag.upper())
    return ""


def classify_frame(img: Image.Image, lenient: bool = False) -> FrameClass:
    """Decide whether the Dota in-game HUD is on screen and read the team names."""
    clock, words = _read_clock(img)
    if clock is None:
        return FrameClass(in_game=False, words=words)

    has_score = _read_score(img, SCORE_BOXES[0]) or _read_score(img, SCORE_BOXES[1])
    if not (has_score or lenient):
        return FrameClass(in_game=False, clock=clock, words=words)

    # Try to get team names from player list (more reliable than top bar logos)
    player_tag = _read_team_tag_from_players(img)
    left_team = player_tag if player_tag else _read_team_name(img, NAME_BOXES[0])
    right_team = _read_team_name(img, NAME_BOXES[1])

    return FrameClass(
        in_game=True,
        clock=clock,
        left_team=left_team,
        right_team=right_team,
        words=words,
    )
