from PIL import Image, ImageDraw, ImageFont

from dota2vod.detect import classify_frame


def hud_frame(font_path: str, text: str, y: int = 12, size: int = 26) -> Image.Image:
    """A 720p frame with a dark top bar carrying `text`, like the Dota HUD."""
    img = Image.new("RGB", (1280, 720), (40, 90, 60))
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, 1280, 56], fill=(18, 18, 22))
    font = ImageFont.truetype(font_path, size)
    w = draw.textlength(text, font=font)
    draw.text(((1280 - w) / 2, y), text, fill=(240, 240, 240), font=font)
    return img


def test_in_game_frame_detected(font_path):
    fc = classify_frame(hud_frame(font_path, "LIQUID  14  32:07  21  SPIRIT"))
    assert fc.in_game
    assert fc.clock and "32" in fc.clock
    assert "LIQUID" in fc.left_team
    assert "SPIRIT" in fc.right_team


def test_negative_clock_pregame(font_path):
    fc = classify_frame(hud_frame(font_path, "FALCONS  0  -0:45  0  TUNDRA"))
    assert fc.in_game
    assert "FALCONS" in fc.left_team
    assert "TUNDRA" in fc.right_team


def test_panel_frame_rejected(font_path):
    img = Image.new("RGB", (1280, 720), (25, 25, 60))
    draw = ImageDraw.Draw(img)
    font = ImageFont.truetype(font_path, 72)
    draw.text((420, 320), "BE RIGHT BACK", fill=(255, 255, 255), font=font)
    assert not classify_frame(img).in_game


def test_countdown_without_scores_rejected_unless_lenient(font_path):
    frame = hud_frame(font_path, "STARTING IN 5:00")
    assert not classify_frame(frame).in_game
    assert classify_frame(frame, lenient=True).in_game
