# dota2vod

Find the individual Dota 2 games inside a full broadcast VOD (YouTube or Twitch)
and print labeled timestamps — **without downloading the video**.

```
$ dota2vod https://www.youtube.com/live/70oVjpTnXzM
Scanning 8:14:03 of video every 45s ...
Game 1  0:21:12 - 1:03:40  (0:42:28)  LIQUID vs SPIRIT
        https://youtu.be/70oVjpTnXzM?t=1272
Game 2  1:18:05 - 1:51:33  (0:33:28)  LIQUID vs SPIRIT
        https://youtu.be/70oVjpTnXzM?t=4685
...
```

## How it works

1. `yt-dlp` resolves a direct stream URL for the VOD (720p by default). Nothing
   is downloaded up front.
2. `ffmpeg` grabs single frames from the stream every 45 s (seeking over HTTP,
   so each probe fetches only a few hundred KB).
3. Each frame is classified **in-game / not in-game** by OCRing the center of
   the top HUD strip with `tesseract`: during a game the spectator HUD always
   shows `TEAM  score  clock  score  TEAM` there. A frame counts as in-game
   when a game clock (`mm:ss`, including the negative pre-horn clock) is found
   flanked by kill-score digits.
4. Consecutive in-game samples are grouped into games; short gaps (pauses,
   quick replays) are merged, short blips (highlight replays on the analyst
   desk) are dropped, and the exact start/end of each game is refined by
   binary-searching frames down to ~5 s.
5. Team names are read from the same HUD strip across several frames per game
   and picked by majority vote.

A typical 8-hour VOD needs ~700 frame probes and finishes in a few minutes.

## Requirements

- Python ≥ 3.10, [uv](https://docs.astral.sh/uv/)
- `ffmpeg`/`ffprobe` and `tesseract-ocr` on `PATH`
  (Debian/Ubuntu: `apt install ffmpeg tesseract-ocr`)

## Install & run

```sh
uv sync
uv run dota2vod <url-or-file> [options]

# or install as a tool
uv tool install .
dota2vod <url-or-file>
```

Works on YouTube VODs, Twitch VODs (both via yt-dlp), and local video files.
Timestamped deep links are generated for YouTube (`?t=1272`) and Twitch
(`?t=0h21m12s`).

### Output formats

- `--format text` (default) — human-readable list with deep links
- `--format chapters` — paste-ready YouTube chapters / comment
- `--format json` — machine-readable, for bots and pipelines

### Useful options

| Flag | Default | Meaning |
| --- | --- | --- |
| `--step` | 45 | coarse sampling interval (s); lower = slower but catches shorter games |
| `--min-game` | 480 | drop detected segments shorter than this (s) |
| `--merge-gap` | 180 | merge in-game segments separated by less than this (s) — covers pauses |
| `--precision` | 5 | boundary refinement precision (s) |
| `--height` | 720 | stream resolution to sample (OCR needs ≥ 480) |
| `--workers` | 8 | parallel frame fetches |
| `--lenient` | off | accept a clock without kill scores next to it (non-standard HUDs) |
| `--start` / `--end` | — | limit the scanned range (s) |
| `--cookies` | — | cookies.txt for yt-dlp, if YouTube asks for sign-in (common on datacenter IPs) |
| `-v` | off | log every sampled frame to stderr |

## Using it as a library (Telegram bot, etc.)

The CLI is a thin wrapper; a bot handler is three calls:

```python
from dota2vod import cli, probe

source = probe.resolve(url)              # raises on bad/live URLs
games = cli.scan(source)                 # list of Segment(start, end, teams)
reply = cli.render_text(source, games)   # or render_json / render_chapters
```

The `Dockerfile` builds a container with all system dependencies
(ffmpeg, tesseract) for running this on Kubernetes:

```sh
docker build -t dota2vod .
docker run --rm dota2vod https://www.youtube.com/live/70oVjpTnXzM --format json
```

## Notes & limitations

- The VOD must be finished processing; live streams are rejected.
- Game starts are detected at the first in-game frame (pre-horn clock), not at
  the start of the draft. If you want drafts included, subtract ~3 min from the
  start or open the link a bit early.
- Team names come from OCR of the HUD tag (e.g. `LIQUID`, `TSPIRIT`) — they are
  what the broadcast shows, not canonical team names.
- Non-standard tournament HUDs that hide the kill score next to the clock need
  `--lenient`.
- YouTube sometimes challenges requests from cloud/datacenter IPs; pass
  `--cookies cookies.txt` (export from your browser) if resolution fails.

## Development

```sh
uv sync --extra dev
uv run pytest                 # unit + OCR tests (~1 s) and e2e (~25 s)
uv run pytest -m "not e2e"    # skip the synthetic-video e2e tests
```

The e2e tests encode a synthetic 700 s broadcast (two "games" with a fake HUD
bar, panels in between) with ffmpeg and assert the pipeline finds both games,
their boundaries within ±4 s, and the right team names.
