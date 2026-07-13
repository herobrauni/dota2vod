"""Turn per-timestamp in-game samples into refined, labeled game segments."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Callable


@dataclass
class Segment:
    start: float
    end: float
    left_team: str = "Unknown"
    right_team: str = "Unknown"

    @property
    def duration(self) -> float:
        return self.end - self.start

    def label(self) -> str:
        if self.left_team == "Unknown" and self.right_team == "Unknown":
            return ""
        return f"{self.left_team} vs {self.right_team}"


def smooth(samples: list[tuple[float, bool]]) -> list[tuple[float, bool]]:
    """Median-of-three filter to kill isolated OCR misses/false positives."""
    if len(samples) < 3:
        return samples
    flags = [f for _, f in samples]
    out = [flags[0]]
    for i in range(1, len(flags) - 1):
        window = (flags[i - 1], flags[i], flags[i + 1])
        out.append(sum(window) >= 2)
    out.append(flags[-1])
    return [(t, f) for (t, _), f in zip(samples, out)]


def group(
    samples: list[tuple[float, bool]],
    merge_gap: float,
    min_duration: float,
) -> list[Segment]:
    """Group consecutive in-game samples into segments.

    Segments separated by less than merge_gap (pauses, disconnect screens,
    quick replays) are merged; segments shorter than min_duration (highlight
    replays on the analyst desk, OCR flukes) are dropped.
    """
    samples = sorted(samples)
    raw: list[Segment] = []
    run_start: float | None = None
    last_true: float | None = None
    for t, in_game in samples:
        if in_game:
            if run_start is None:
                run_start = t
            last_true = t
        elif run_start is not None:
            raw.append(Segment(run_start, last_true))
            run_start = None
    if run_start is not None and last_true is not None:
        raw.append(Segment(run_start, last_true))

    merged: list[Segment] = []
    for seg in raw:
        if merged and seg.start - merged[-1].end <= merge_gap:
            merged[-1].end = seg.end
        else:
            merged.append(seg)
    return [s for s in merged if s.duration >= min_duration]


def refine_boundary(
    lo: float,
    hi: float,
    lo_in_game: bool,
    probe: Callable[[float], bool],
    precision: float,
) -> float:
    """Binary-search the in-game/not-in-game transition between lo and hi.

    lo_in_game says which side of the transition lo sits on; returns the
    earliest known time on the hi side (within precision).
    """
    result = hi
    while hi - lo > precision:
        mid = (lo + hi) / 2
        if probe(mid) != lo_in_game:
            result = mid
            hi = mid
        else:
            lo = mid
    return result


def pick_team_names(
    votes: list[tuple[str, str]], fallback: str = "Unknown"
) -> tuple[str, str]:
    """Choose the majority OCR reading for each side.

    Most tournament HUDs show team logos, not names; OCRing the logo slot then
    yields sporadic junk ('RE', 'THE', ...). A real text tag reads the same on
    nearly every frame, so demand the winner appears in most votes (and at
    least twice) before trusting it.
    """

    def best(names: list[str]) -> str:
        counts = Counter(n for n in names if n)
        if not counts:
            return fallback
        name, count = counts.most_common(1)[0]
        # Require at least 2 votes and 25% of votes (was 50%)
        if count < 2 or count * 4 < len(names):
            return fallback
        return name

    return best([v[0] for v in votes]), best([v[1] for v in votes])
