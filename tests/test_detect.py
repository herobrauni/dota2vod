from PIL import Image, ImageDraw, ImageFont

from dota2vod.detect import classify_frame

# Geometry of the synthetic HUD mirrors the real 720p spectator bar: tiny
# clock centered at ~y18, kill scores in slots at x~0.462 / x~0.538, and
# (only for teams without a logo) a text tag out at x~0.235 / x~0.765.
W, H = 1280, 720


def hud_frame(
    font_path: str,
    clock: str = "32:07",
    left_score: str | None = "14",
    right_score: str | None = "21",
    left_name: str | None = None,
    right_name: str | None = None,
) -> Image.Image:
    img = Image.new("RGB", (W, H), (40, 90, 60))
    draw = ImageDraw.Draw(img)
    draw.rectangle([int(W * 0.18), 0, int(W * 0.82), 40], fill=(18, 18, 22))

    def centered(text, cx, y, size):
        font = ImageFont.truetype(font_path, size)
        tw = draw.textlength(text, font=font)
        draw.text((cx - tw / 2, y), text, fill=(240, 240, 240), font=font)

    centered(clock, W * 0.500, 13, 15)
    if left_score:
        centered(left_score, W * 0.462, 8, 20)
    if right_score:
        centered(right_score, W * 0.538, 8, 20)
    if left_name:
        centered(left_name, W * 0.235, 10, 18)
    if right_name:
        centered(right_name, W * 0.765, 10, 18)
    return img


def test_in_game_frame_detected(font_path):
    fc = classify_frame(hud_frame(font_path))
    assert fc.in_game
    assert fc.clock and "32" in fc.clock


def test_negative_clock_pregame(font_path):
    fc = classify_frame(hud_frame(font_path, clock="-0:45", left_score="0", right_score="0"))
    assert fc.in_game


def test_team_name_text_read_when_present(font_path):
    fc = classify_frame(hud_frame(font_path, left_name="LIQUID", right_name="SPIRIT"))
    assert fc.in_game
    assert "LIQUID" in fc.left_team
    assert "SPIRIT" in fc.right_team


def test_logo_only_hud_gives_empty_names(font_path):
    fc = classify_frame(hud_frame(font_path))
    assert fc.in_game
    assert fc.left_team == ""
    assert fc.right_team == ""


def test_panel_frame_rejected(font_path):
    img = Image.new("RGB", (W, H), (25, 25, 60))
    draw = ImageDraw.Draw(img)
    font = ImageFont.truetype(font_path, 72)
    draw.text((420, 320), "BE RIGHT BACK", fill=(255, 255, 255), font=font)
    assert not classify_frame(img).in_game


def test_countdown_without_scores_rejected_unless_lenient(font_path):
    frame = hud_frame(font_path, clock="5:00", left_score=None, right_score=None)
    assert not classify_frame(frame).in_game
    assert classify_frame(frame, lenient=True).in_game
