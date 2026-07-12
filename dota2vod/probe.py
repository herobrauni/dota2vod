"""Resolve VOD sources and grab single frames without downloading the video."""

from __future__ import annotations

import io
import json
import os
import subprocess
from dataclasses import dataclass, field

from PIL import Image


class ProbeError(RuntimeError):
    pass


@dataclass
class Source:
    """A video we can grab frames from: either a remote stream URL or a local file."""

    stream_url: str
    duration: float
    title: str = ""
    video_id: str = ""
    extractor: str = ""  # "Youtube", "TwitchVod", "" for local files
    webpage_url: str = ""
    http_headers: dict[str, str] = field(default_factory=dict)

    def timestamp_url(self, t: float) -> str | None:
        """Deep link into the VOD at second t, if the platform supports it."""
        secs = max(0, int(t))
        ex = self.extractor.lower()
        if ex.startswith("youtube") and self.video_id:
            return f"https://youtu.be/{self.video_id}?t={secs}"
        if ex.startswith("twitch") and self.video_id:
            h, rem = divmod(secs, 3600)
            m, s = divmod(rem, 60)
            return f"https://www.twitch.tv/videos/{self.video_id}?t={h}h{m}m{s}s"
        return None


def _ffprobe_duration(path: str) -> float:
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            path,
        ],
        capture_output=True,
        text=True,
    )
    if out.returncode != 0:
        raise ProbeError(f"ffprobe failed for {path}: {out.stderr.strip()}")
    return float(json.loads(out.stdout)["format"]["duration"])


def resolve(target: str, height: int = 720, cookies: str | None = None) -> Source:
    """Turn a URL or local path into a Source we can grab frames from.

    For URLs, yt-dlp picks a video-only stream at or below `height`; nothing is
    downloaded here, we only resolve the direct stream URL.
    """
    if os.path.exists(target):
        return Source(stream_url=target, duration=_ffprobe_duration(target))

    import yt_dlp

    fmt = (
        f"bv*[height<={height}][protocol*=m3u8]"
        f"/bv*[height<={height}]/b[height<={height}]/bv*/b"
    )
    opts: dict = {
        "format": fmt,
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
    }
    if cookies:
        opts["cookiefile"] = cookies
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(target, download=False)
    if info is None:
        raise ProbeError(f"yt-dlp could not resolve {target}")
    if "entries" in info:
        info = info["entries"][0]
    if info.get("is_live"):
        raise ProbeError("Stream is still live; wait for the VOD to finalize.")

    chosen = (info.get("requested_formats") or [info])[0]
    stream_url = chosen.get("url")
    if not stream_url:
        raise ProbeError("yt-dlp returned no stream URL for the requested format")
    duration = info.get("duration")
    if not duration:
        duration = _ffprobe_duration(stream_url)
    return Source(
        stream_url=stream_url,
        duration=float(duration),
        title=info.get("title") or "",
        video_id=info.get("id") or "",
        extractor=info.get("extractor_key") or "",
        webpage_url=info.get("webpage_url") or target,
        http_headers=chosen.get("http_headers") or info.get("http_headers") or {},
    )


def grab_frame(source: Source, t: float, retries: int = 2) -> Image.Image | None:
    """Fetch a single frame at second t. Returns None if the grab fails."""
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", f"{t:.2f}"]
    if source.http_headers:
        hdrs = "".join(f"{k}: {v}\r\n" for k, v in source.http_headers.items())
        cmd += ["-headers", hdrs]
    cmd += [
        "-i",
        source.stream_url,
        "-frames:v",
        "1",
        "-f",
        "image2pipe",
        "-c:v",
        "png",
        "-",
    ]
    for _ in range(retries + 1):
        try:
            out = subprocess.run(cmd, capture_output=True, timeout=120)
        except subprocess.TimeoutExpired:
            continue
        if out.returncode == 0 and out.stdout:
            try:
                return Image.open(io.BytesIO(out.stdout)).convert("RGB")
            except OSError:
                continue
    return None
