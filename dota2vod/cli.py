"""Command-line entry point: scan a VOD and print labeled game timestamps."""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor

from . import detect, probe, segments
from .probe import Source
from .segments import Segment


def hms(t: float) -> str:
    secs = max(0, int(t))
    h, rem = divmod(secs, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}"


class Scanner:
    def __init__(self, source: Source, lenient: bool, workers: int, verbose: bool):
        self.source = source
        self.lenient = lenient
        self.workers = workers
        self.verbose = verbose
        self._cache: dict[float, detect.FrameClass] = {}

    def classify_at(self, t: float) -> detect.FrameClass:
        t = round(t, 2)
        if t not in self._cache:
            img = probe.grab_frame(self.source, t)
            self._cache[t] = (
                detect.FrameClass(in_game=False)
                if img is None
                else detect.classify_frame(img, lenient=self.lenient)
            )
        return self._cache[t]

    def coarse_scan(self, start: float, end: float, step: float) -> list[tuple[float, bool]]:
        times = []
        t = start
        while t < end:
            times.append(round(t, 2))
            t += step
        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            results = list(pool.map(self.classify_at, times))
        if self.verbose:
            for t, fc in zip(times, results):
                mark = "GAME" if fc.in_game else "----"
                extra = f" clock={fc.clock} {fc.left_team!r} vs {fc.right_team!r}" if fc.clock else ""
                print(f"  {hms(t)} {mark}{extra}", file=sys.stderr)
        return [(t, fc.in_game) for t, fc in zip(times, results)]

    def fast_coarse_scan(self, start: float, end: float, coarse_step: float = 600.0) -> list[tuple[float, bool]]:
        """Fast first pass with large intervals (default 10 min = 600s)."""
        return self.coarse_scan(start, end, coarse_step)

    def refine_game_boundaries(self, segs: list[Segment], fine_step: float = 30.0) -> list[Segment]:
        """For each detected game segment, refine its start/end boundaries using finer sampling."""
        refined = []
        for seg in segs:
            # Find exact start: binary search backwards from seg.start
            # Look back enough to catch the pre-game (draft, horn)
            lo = max(0, seg.start - fine_step * 4)
            hi = seg.start
            while hi - lo > 5:  # 5 second precision
                mid = (lo + hi) / 2
                if self.classify_at(mid).in_game:
                    hi = mid
                else:
                    lo = mid
            seg.start = hi

            # Find exact end: binary search forwards from seg.end
            lo = seg.end
            hi = min(self.source.duration, seg.end + fine_step * 4)
            while hi - lo > 5:
                mid = (lo + hi) / 2
                if self.classify_at(mid).in_game:
                    lo = mid
                else:
                    hi = mid
            seg.end = lo

            # Verify minimum duration (20 min = 1200s)
            if seg.duration >= 1200:
                refined.append(seg)
        return refined

    def refine(self, segs: list[Segment], step: float, precision: float) -> None:
        for seg in segs:
            seg.start = segments.refine_boundary(
                lo=max(0.0, seg.start - step),
                hi=seg.start,
                lo_in_game=False,
                probe=lambda t: self.classify_at(t).in_game,
                precision=precision,
            )
            seg.end = segments.refine_boundary(
                lo=seg.end,
                hi=min(seg.end + step, self.source.duration),
                lo_in_game=True,
                probe=lambda t: self.classify_at(t).in_game,
                precision=precision,
            )

    def name_teams(self, seg: Segment, n_samples: int = 12) -> None:
        votes: list[tuple[str, str]] = []
        # Reuse anything already classified inside the segment.
        for t, fc in self._cache.items():
            if seg.start <= t <= seg.end and fc.in_game:
                votes.append((fc.left_team, fc.right_team))
        # Add fresh interior samples (skip the edges: drafts/loading screens).
        span = seg.duration
        times = [seg.start + span * (0.2 + 0.6 * i / max(1, n_samples - 1)) for i in range(n_samples)]
        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            for fc in pool.map(self.classify_at, times):
                if fc.in_game:
                    votes.append((fc.left_team, fc.right_team))
        seg.left_team, seg.right_team = segments.pick_team_names(votes)


def fast_scan(
    source: Source,
    start: float = 0.0,
    end: float | None = None,
    coarse_step: float = 600.0,   # 10 minutes
    fine_step: float = 30.0,      # 30 seconds for refinement
    min_game: float = 1200.0,     # 20 minutes minimum
    lenient: bool = False,
    workers: int = 8,
    verbose: bool = False,
) -> list[Segment]:
    """Fast two-pass scan: coarse detection at 10-min intervals, then refine boundaries."""
    end = min(end, source.duration) if end else source.duration
    scanner = Scanner(source, lenient=lenient, workers=workers, verbose=verbose)

    print(f"Fast scan: checking every {hms(coarse_step)}...", file=sys.stderr)
    samples = scanner.fast_coarse_scan(start, end, coarse_step)
    segs = segments.group(segments.smooth(samples), merge_gap=coarse_step, min_duration=min_game/2)

    print(f"Found {len(segs)} game candidates, refining boundaries...", file=sys.stderr)
    segs = scanner.refine_game_boundaries(segs, fine_step)

    for seg in segs:
        scanner.name_teams(seg)
    return segs


def scan(
    source: Source,
    step: float = 45.0,
    start: float = 0.0,
    end: float | None = None,
    merge_gap: float = 180.0,
    min_game: float = 480.0,
    precision: float = 5.0,
    lenient: bool = False,
    workers: int = 8,
    verbose: bool = False,
) -> list[Segment]:
    end = min(end, source.duration) if end else source.duration
    scanner = Scanner(source, lenient=lenient, workers=workers, verbose=verbose)
    samples = scanner.coarse_scan(start, end, step)
    segs = segments.group(segments.smooth(samples), merge_gap=merge_gap, min_duration=min_game)
    scanner.refine(segs, step=step, precision=precision)
    for seg in segs:
        scanner.name_teams(seg)
    return segs


def render_text(source: Source, segs: list[Segment]) -> str:
    lines = []
    if source.title:
        lines.append(source.title)
        lines.append("")
    if not segs:
        lines.append("No games detected.")
    for i, seg in enumerate(segs, 1):
        line = f"Game {i}  {hms(seg.start)} - {hms(seg.end)}  ({hms(seg.duration)})"
        if seg.label():
            line += f"  {seg.label()}"
        lines.append(line)
        link = source.timestamp_url(seg.start)
        if link:
            lines.append(f"        {link}")
    return "\n".join(lines)


def render_chapters(source: Source, segs: list[Segment]) -> str:
    lines = ["0:00 Stream start"]
    for i, seg in enumerate(segs, 1):
        label = f" - {seg.label()}" if seg.label() else ""
        lines.append(f"{hms(seg.start)} Game {i}{label}")
    return "\n".join(lines)


def render_json(source: Source, segs: list[Segment]) -> str:
    return json.dumps(
        {
            "title": source.title,
            "url": source.webpage_url,
            "duration": source.duration,
            "games": [
                {
                    "game": i,
                    "start": round(seg.start, 1),
                    "end": round(seg.end, 1),
                    "start_hms": hms(seg.start),
                    "end_hms": hms(seg.end),
                    "left_team": seg.left_team,
                    "right_team": seg.right_team,
                    "link": source.timestamp_url(seg.start),
                }
                for i, seg in enumerate(segs, 1)
            ],
        },
        indent=2,
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="dota2vod",
        description="Find Dota 2 games in a broadcast VOD (YouTube/Twitch URL or local file) "
        "and print labeled timestamps. Nothing is downloaded; frames are sampled remotely.",
    )
    p.add_argument("url", help="YouTube/Twitch VOD URL or path to a local video file")
    p.add_argument("--format", choices=["text", "chapters", "json"], default="text")
    p.add_argument("--step", type=float, default=45.0, help="coarse sampling interval, seconds (default 45)")
    p.add_argument("--start", type=float, default=0.0, help="only scan from this second onward")
    p.add_argument("--end", type=float, default=None, help="only scan up to this second")
    p.add_argument("--min-game", type=float, default=480.0, help="drop segments shorter than this, seconds (default 480)")
    p.add_argument("--merge-gap", type=float, default=180.0, help="merge segments separated by less than this (pauses), seconds (default 180)")
    p.add_argument("--precision", type=float, default=5.0, help="boundary refinement precision, seconds (default 5)")
    p.add_argument("--height", type=int, default=720, help="stream resolution to sample (default 720)")
    p.add_argument("--workers", type=int, default=8, help="parallel frame fetches (default 8)")
    p.add_argument("--lenient", action="store_true", help="accept a game clock without kill scores next to it")
    p.add_argument("--cookies", help="cookies.txt file passed to yt-dlp (if YouTube asks for sign-in)")
    p.add_argument("--fast", action="store_true", help="use fast two-pass scan (20-min intervals, ~1 min for 6h VOD)")
    p.add_argument("-v", "--verbose", action="store_true", help="log every sampled frame to stderr")
    args = p.parse_args(argv)

    try:
        source = probe.resolve(args.url, height=args.height, cookies=args.cookies)
    except Exception as e:  # noqa: BLE001 - surface resolver errors cleanly
        print(f"error: could not resolve {args.url}: {e}", file=sys.stderr)
        return 1

    print(
        f"Scanning {hms(source.duration)} of video every {args.step:.0f}s ...",
        file=sys.stderr,
    )

    if args.fast:
        segs = fast_scan(
            source,
            start=args.start,
            end=args.end,
            coarse_step=600.0,
            fine_step=30.0,
            min_game=1200.0,
            lenient=args.lenient,
            workers=args.workers,
            verbose=args.verbose,
        )
    else:
        segs = scan(
            source,
            step=args.step,
            start=args.start,
            end=args.end,
            merge_gap=args.merge_gap,
            min_game=args.min_game,
            precision=args.precision,
            lenient=args.lenient,
            workers=args.workers,
            verbose=args.verbose,
        )
    renderer = {"text": render_text, "chapters": render_chapters, "json": render_json}[args.format]
    print(renderer(source, segs))
    return 0


if __name__ == "__main__":
    sys.exit(main())
