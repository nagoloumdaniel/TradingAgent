"""The brand mark in both themes: a white glyph on a light canvas is an invisible logo.

``nexagold.png`` is white ink on a transparent background — correct on the dark default,
all but invisible once the operator switches to the light theme. ``nexagold-dark.png`` is
the same glyph with its luminance inverted and its alpha channel carried over untouched.

These tests decode both files with the standard library alone (``zlib`` + PNG unfiltering):
"the light-theme mark is dark" is measured on the real pixels, and "the transparency was
preserved" is proved by comparing the two alpha channels byte for byte — no Pillow, no
dependency for a test.
"""

import re
import struct
import zlib
from pathlib import Path

from fastapi.testclient import TestClient

WEB_STATIC = Path(__file__).resolve().parents[2] / "src" / "tradingagent" / "web" / "static"
DARK_MARK = WEB_STATIC / "nexagold.png"
LIGHT_MARK = WEB_STATIC / "nexagold-dark.png"

# Rec. 709 luminance, the weights the generation used.
LUMA = (0.2126, 0.7152, 0.0722)
# The white glyph measures ~250; the inverted one must be its mirror, and no plausible
# anti-aliased edge may drag it back above this.
LIGHT_THEME_MAX_LUMA = 60.0
DARK_THEME_MIN_LUMA = 200.0

Pixel = tuple[int, int, int, int]


def _paeth(left: int, up: int, up_left: int) -> int:
    estimate = left + up - up_left
    dl, du, dul = abs(estimate - left), abs(estimate - up), abs(estimate - up_left)
    if dl <= du and dl <= dul:
        return left
    return up if du <= dul else up_left


def _unfilter(kind: int, line: bytearray, previous: bytes, bpp: int) -> None:
    """Undo one PNG row filter in place. ``line`` is the already-reconstructed prefix."""
    for index in range(len(line)):
        left = line[index - bpp] if index >= bpp else 0
        up = previous[index]
        up_left = previous[index - bpp] if index >= bpp else 0
        if kind == 0:
            continue
        if kind == 1:
            line[index] = (line[index] + left) & 0xFF
        elif kind == 2:
            line[index] = (line[index] + up) & 0xFF
        elif kind == 3:
            line[index] = (line[index] + ((left + up) >> 1)) & 0xFF
        elif kind == 4:
            line[index] = (line[index] + _paeth(left, up, up_left)) & 0xFF
        else:  # pragma: no cover - a filter outside 0..4 is not a PNG
            raise AssertionError(f"unknown PNG row filter {kind}")


def _chunks(data: bytes) -> list[tuple[bytes, bytes]]:
    """Every PNG chunk as ``(type, payload)``, after checking the signature."""
    assert data[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG file"
    found: list[tuple[bytes, bytes]] = []
    offset = 8
    while offset < len(data):
        (length,) = struct.unpack(">I", data[offset : offset + 4])
        kind = data[offset + 4 : offset + 8]
        found.append((kind, data[offset + 8 : offset + 8 + length]))
        offset += 12 + length
    return found


def read_rgba(path: Path) -> tuple[int, int, list[Pixel]]:
    """Decode an 8-bit RGBA, non-interlaced PNG into ``(width, height, pixels)``."""
    data = path.read_bytes()
    chunks = dict(_chunks(data))
    width, height, depth, colour, compression, method, interlace = struct.unpack(
        ">IIBBBBB", chunks[b"IHDR"]
    )
    assert depth == 8, f"{path.name}: only 8-bit channels are supported"
    assert colour == 6, f"{path.name}: the brand mark must keep an alpha channel"
    assert (compression, method, interlace) == (0, 0, 0), f"{path.name}: unexpected PNG flags"
    raw = zlib.decompress(b"".join(payload for kind, payload in _chunks(data) if kind == b"IDAT"))
    stride = width * 4
    assert len(raw) == height * (stride + 1), f"{path.name}: truncated image data"
    pixels: list[Pixel] = []
    previous = bytes(stride)
    for row in range(height):
        start = row * (stride + 1)
        line = bytearray(raw[start + 1 : start + 1 + stride])
        _unfilter(raw[start], line, previous, 4)
        previous = bytes(line)
        pixels.extend(
            (line[index], line[index + 1], line[index + 2], line[index + 3])
            for index in range(0, stride, 4)
        )
    return width, height, pixels


def luminance(pixel: Pixel) -> float:
    return LUMA[0] * pixel[0] + LUMA[1] * pixel[1] + LUMA[2] * pixel[2]


def opaque(pixels: list[Pixel]) -> list[Pixel]:
    return [pixel for pixel in pixels if pixel[3] > 0]


def mean_luminance(pixels: list[Pixel]) -> float:
    visible = opaque(pixels)
    assert visible, "the mark must draw something"
    return sum(luminance(pixel) for pixel in visible) / len(visible)


def test_both_variants_are_in_the_package() -> None:
    assert DARK_MARK.is_file(), "the dark-theme mark is the one the shell already shipped"
    assert LIGHT_MARK.is_file(), "the light theme needs its own file, not a CSS trick"


def test_the_two_variants_share_their_geometry() -> None:
    assert read_rgba(DARK_MARK)[:2] == read_rgba(LIGHT_MARK)[:2]


def test_the_light_variant_is_dark() -> None:
    _, _, pixels = read_rgba(LIGHT_MARK)

    assert mean_luminance(pixels) < LIGHT_THEME_MAX_LUMA


def test_the_reference_mark_is_white() -> None:
    """The threshold only means something next to the mark it was derived from."""
    _, _, pixels = read_rgba(DARK_MARK)

    assert mean_luminance(pixels) > DARK_THEME_MIN_LUMA


def test_the_light_variant_carries_the_same_transparency() -> None:
    """Inverting the ink must not flatten the alpha channel into a black square."""
    _, _, white_ink = read_rgba(DARK_MARK)
    _, _, dark_ink = read_rgba(LIGHT_MARK)

    assert [pixel[3] for pixel in white_ink] == [pixel[3] for pixel in dark_ink]
    assert sum(1 for pixel in dark_ink if pixel[3] == 0) > 100_000


def test_the_mark_is_served_read_only(client: TestClient) -> None:
    served = client.get("/static/nexagold-dark.png")

    assert served.status_code == 200
    assert served.headers["content-type"].startswith("image/png")
    assert served.content[:8] == b"\x89PNG\r\n\x1a\n"
    for method in ("post", "put", "patch", "delete"):
        assert getattr(client, method)("/static/nexagold-dark.png").status_code == 405


def test_the_shell_switches_the_mark_with_the_theme(client: TestClient) -> None:
    body = client.get("/").text

    assert 'src="/static/nexagold.png"' in body
    assert 'src="/static/nexagold-dark.png"' in body
    # Each mark is declared once, and each is tied to exactly one theme: the stylesheet is
    # the only thing that decides which one is drawn, so there is no flash and no script.
    assert body.count('src="/static/nexagold.png"') == 1
    assert body.count('src="/static/nexagold-dark.png"') == 1
    assert re.search(
        r':root\[data-theme="light"\]\s+\.brand \.brand-mark-dark\s*\{\s*display:\s*none',
        body,
    )
    assert re.search(
        r':root\[data-theme="light"\]\s+\.brand \.brand-mark-light\s*\{\s*display:\s*block',
        body,
    )
