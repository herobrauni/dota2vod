# AGENTS.md

Guidance for AI agents (and humans) working on this repo.

## What this project is

`dota2vod` finds the individual Dota 2 games inside a full broadcast VOD
(YouTube/Twitch URL or local file) and outputs labeled timestamps with deep
links — **without downloading the video**. See README.md for user-facing docs.

Planned direction: wrap the library in a Telegram bot (send a VOD URL, get
timestamp links back) deployed on Kubernetes via the existing Dockerfile.

## How it works (pipeline)

```
URL ──yt-dlp──▶ stream URL ──ffmpeg -ss──▶ single frames every 45s
        ──tesseract OCR of top HUD strip──▶ in-game? + team names
        ──group/merge/filter──▶ segments ──binary-search refine──▶ timestamps
```

A frame is "in-game" when the spectator HUD top bar (center of the top ~8.5%
of the frame) contains a game clock (`mm:ss`, possibly negative pre-horn)
flanked by kill-score digits. Words left/right of the clock are the team names.

## Layout

| File | Responsibility |
| --- | --- |
| `dota2vod/probe.py` | `resolve()` URL/file → `Source`; `grab_frame()` via ffmpeg. All network/subprocess I/O for video. |
| `dota2vod/detect.py` | Pure image → `FrameClass` (in-game?, clock, team names). tesseract subprocess lives here. |
| `dota2vod/segments.py` | Pure logic: smoothing, grouping, gap merging, boundary binary search, team-name majority vote. No I/O — keep it that way, it's the easily-testable core. |
| `dota2vod/cli.py` | Orchestration (`scan()`), renderers (`render_text/chapters/json`), argparse `main()`. A future Telegram bot should call `probe.resolve` + `cli.scan` + a renderer, not shell out to the CLI. |

## Commands

Everything runs through [uv](https://docs.astral.sh/uv/) — do not use bare
pip/python:

```sh
uv sync --extra dev            # set up venv (commits uv.lock; keep it updated)
uv run pytest                  # full suite, ~25 s
uv run pytest -m "not e2e"     # fast unit tests only, ~1 s
uv run dota2vod <url-or-file>  # run the CLI
```

System deps (must be on PATH): `ffmpeg`, `ffprobe`, `tesseract`.
On Debian/Ubuntu: `apt install ffmpeg tesseract-ocr`. The Dockerfile has them.

## Testing philosophy

Real VODs can't be fetched in CI or in sandboxed Claude environments (YouTube
is usually blocked / challenges datacenter IPs), so correctness is proven
against **synthetic footage**:

- `tests/test_detect.py` draws fake HUD frames with PIL and runs real tesseract.
- `tests/test_e2e.py` encodes a 700 s synthetic broadcast with ffmpeg
  (two "games" with HUD bar + clock, panels in between) and asserts the full
  pipeline finds both games, boundaries within ±4 s, and correct team names.
- `tests/test_segments.py` covers the pure grouping/refinement logic.

When changing detection heuristics, extend the synthetic tests to cover the
new case. If you get access to real VOD frames, prefer adding a small set of
cropped top-strip PNGs as fixtures over full frames (size, rights).

## Gotchas (learned the hard way)

- **ffmpeg drawtext**: `:` is an option separator; clock text in test filters
  must be escaped (`32\:07`). Truncated HUD text = silently failing OCR.
- **tesseract + parallelism**: always run with `OMP_THREAD_LIMIT=1`
  (done in `detect._ocr_words`). Without it, N parallel tesseract processes
  thrash OpenMP threads — 10-20x slowdown and timeouts.
- **OCR of ':'**: sometimes reads as `.`; `CLOCK_RE` accepts both. Team-name
  OCR is noisy — that's why names are majority-voted across frames.
- **yt-dlp**: live (unfinished) streams are rejected in `probe.resolve`.
  YouTube may demand cookies from datacenter IPs → `--cookies` flag exists.
- **HUD assumptions**: crop constants (`STRIP_HEIGHT_FRAC`, `STRIP_WIDTH_FRAC`
  in `detect.py`) assume the standard Valve spectator HUD. Tournament overlays
  that hide kill scores need `--lenient`. If detection fails on real footage,
  debug by saving `detect.crop_strip(frame)` output and looking at it.

## Conventions

- Python ≥ 3.10, type hints on public functions, dataclasses over dicts.
- Keep `segments.py` pure (no subprocess/network) and `detect.py` free of
  video-source knowledge; only `probe.py` talks to yt-dlp/ffmpeg inputs.
- New CLI flags: add to README's options table.
- Don't add heavyweight deps (opencv, torch) without a strong reason —
  the tool should stay a slim container.

## Roadmap / open ideas

- [ ] Telegram bot entry point (`dota2vod/bot.py`?, aiogram or
      python-telegram-bot), reading the token from env; reuse `cli.scan`.
- [ ] k8s manifests / Helm chart next to the Dockerfile.
- [ ] Validate against a real tournament VOD; tune crop constants if needed.
- [ ] Optional draft detection (draft screen shows team names + countdown) to
      include picks/bans in the segment.
- [ ] Caching layer keyed by video ID so repeat requests are instant.
