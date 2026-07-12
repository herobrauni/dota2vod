import shutil

import pytest

FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
]


def find_font() -> str | None:
    import os

    for path in FONT_CANDIDATES:
        if os.path.exists(path):
            return path
    return None


@pytest.fixture
def font_path():
    path = find_font()
    if path is None:
        pytest.skip("no known truetype font available")
    return path


@pytest.fixture(autouse=True)
def require_tesseract():
    if shutil.which("tesseract") is None:
        pytest.skip("tesseract not installed")
