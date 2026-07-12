"""End-to-end: encode a synthetic broadcast VOD, run the full pipeline on it.

Timeline of the synthetic stream (700s):
    0-100    talking heads / starting soon panel
  100-350    game 1: LIQUID vs SPIRIT (HUD bar with clock+scores)
  350-450    break panel
  450-650    game 2: FALCONS vs TUNDRA
  650-700    outro panel
"""

import subprocess

import pytest

from dota2vod import cli, probe

GAME1 = (100, 350, "LIQUID  14  32:07  21  SPIRIT")
GAME2 = (450, 650, "FALCONS  5  12:44  9  TUNDRA")


@pytest.fixture(scope="module")
def synthetic_vod(tmp_path_factory):
    from conftest import find_font

    font = find_font()
    if font is None:
        pytest.skip("no truetype font for drawtext")
    path = tmp_path_factory.mktemp("vod") / "stream.mp4"

    def hud(start, end, text):
        common = f"enable='between(t,{start},{end})'"
        box = f"drawbox=x=0:y=0:w=iw:h=56:color=0x121216:t=fill:{common}"
        # ':' is an option separator inside drawtext, escape it in the clock
        txt = (
            f"drawtext=fontfile={font}:text='{text.replace(':', chr(92) + ':')}':"
            f"fontsize=26:fontcolor=white:x=(w-text_w)/2:y=14:{common}"
        )
        return f"{box},{txt}"

    panel = (
        f"drawtext=fontfile={font}:text='BE RIGHT BACK':fontsize=72:fontcolor=white:"
        f"x=(w-text_w)/2:y=(h-text_h)/2:"
        f"enable='lt(t,{GAME1[0]})+between(t,{GAME1[1]},{GAME2[0]})+gt(t,{GAME2[1]})'"
    )
    vf = ",".join([panel, hud(*GAME1), hud(*GAME2)])
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "color=c=0x28465a:size=1280x720:rate=2:duration=700",
        "-vf", vf,
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "30",
        str(path),
    ]
    subprocess.run(cmd, check=True, timeout=300)
    return str(path)


@pytest.mark.e2e
def test_full_pipeline_on_synthetic_vod(synthetic_vod):
    source = probe.resolve(synthetic_vod)
    assert abs(source.duration - 700) < 2

    segs = cli.scan(
        source,
        step=20,
        merge_gap=60,
        min_game=120,
        precision=2.0,
        workers=8,
    )

    assert len(segs) == 2
    for seg, (start, end, text) in zip(segs, (GAME1, GAME2)):
        assert abs(seg.start - start) <= 4, f"start {seg.start} vs expected {start}"
        assert abs(seg.end - end) <= 4, f"end {seg.end} vs expected {end}"
    assert "LIQUID" in segs[0].left_team
    assert "SPIRIT" in segs[0].right_team
    assert "FALCONS" in segs[1].left_team
    assert "TUNDRA" in segs[1].right_team


@pytest.mark.e2e
def test_cli_output_formats(synthetic_vod, capsys):
    rc = cli.main([synthetic_vod, "--step", "20", "--merge-gap", "60",
                   "--min-game", "120", "--precision", "2", "--format", "chapters"])
    assert rc == 0
    out = capsys.readouterr().out
    assert out.startswith("0:00 Stream start")
    assert "Game 1" in out and "Game 2" in out
