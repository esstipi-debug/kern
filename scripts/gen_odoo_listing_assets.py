"""Generate Odoo Apps Store listing images (icon, cover, two screenshots).

Stdlib only (no Pillow). Run from repo root:

    python scripts/gen_odoo_listing_assets.py

Writes identical PNGs into odoo_addon/{17.0,19.0}/kern_dry_run/static/description/.
These are product mockups of the wizard (form title + result table match the
real view strings) -- not captures of a live Odoo db.
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_INK = (8, 11, 17)
_TEAL = (79, 209, 197)
_TEAL_DK = (15, 80, 78)
_PANEL = (17, 23, 34)
_LINE = (30, 39, 51)
_TXT = (231, 238, 246)
_MUTED = (154, 167, 182)
_WHITE = (255, 255, 255)
_WARM = (255, 181, 69)


def _png(width: int, height: int, rgb: bytes) -> bytes:
    """Unfiltered RGB PNG."""
    assert len(rgb) == width * height * 3
    raw = bytearray()
    row = width * 3
    for y in range(height):
        raw.append(0)
        raw.extend(rgb[y * row : (y + 1) * row])
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + chunk(b"IEND", b"")
    )


def _fill(buf: bytearray, w: int, h: int, color: tuple[int, int, int]) -> None:
    r, g, b = color
    for i in range(0, w * h * 3, 3):
        buf[i] = r
        buf[i + 1] = g
        buf[i + 2] = b


def _rect(
    buf: bytearray,
    w: int,
    x0: int,
    y0: int,
    x1: int,
    y1: int,
    color: tuple[int, int, int],
) -> None:
    r, g, b = color
    for y in range(max(0, y0), y1):
        off = y * w * 3
        for x in range(max(0, x0), x1):
            i = off + x * 3
            buf[i] = r
            buf[i + 1] = g
            buf[i + 2] = b


def _glyph_5x7() -> dict[str, str]:
    # 5x7 bitmap font, 1 = pixel on. Enough for listing mockups.
    return {
        " ": "00000" * 7,
        "A": "0111010001111111000110001100011",
        "B": "111101000111110100011000111110",
        "C": "0111110000100001000010000011111",
        "D": "111101000110001100011000111110",
        "E": "1111110000111101000010000111111",
        "F": "111111000011110100001000010000",
        "G": "011111000010000101111000101111",
        "H": "1000110001111111000110001100011",
        "I": "111110010000100001000010011111",
        "K": "1000110010111001010010010100011",
        "L": "1000010000100001000010000111111",
        "M": "1000111011101011000110001100011",
        "N": "1000111001101011001110001100011",
        "O": "011101000110001100011000101110",
        "P": "111101000111110100001000010000",
        "Q": "0111010001100011000110010011101",
        "R": "1111010001111101001010001100011",
        "S": "011111000001110000010000111110",
        "T": "111110010000100001000010000100",
        "U": "100011000110001100011000101110",
        "V": "100011000110001010100101000100",
        "W": "1000110001100011010110101100011",
        "Y": "100011000101010001000010000100",
        "a": "0000001110100010111101000101111",
        "c": "000000111110000100001000001111",
        "d": "000010000101111100011000101111",
        "e": "000000111010001111101000001111",
        "f": "001100100011110010000100001000",
        "g": "000000111110001011110000101110",
        "h": "100001000011110100011000110001",
        "i": "001000000001100001000010001110",
        "k": "100001000010010101000110001001",
        "l": "011000010000100001000010000110",
        "m": "000001101010101101011010110101",
        "n": "000001111010001100011000110001",
        "o": "000000111010001100011000101110",
        "p": "000001111010001111101000010000",
        "r": "000001011011000100001000010000",
        "s": "000000111110000011100000111110",
        "t": "010000100011110010000100000110",
        "u": "000001000110001100011000101111",
        "v": "000001000110001010100101000010",
        "w": "000001000110101101011010101010",
        "y": "000001000110001011110000101110",
        "0": "011101000110101101011000101110",
        "1": "001000110000100001000010011111",
        "2": "011101000100010001000100011111",
        "3": "011101000100110000011000101110",
        "4": "000100011001010111110001000010",
        "5": "111111000011110000010000111110",
        "6": "011111000011110100011000101110",
        "7": "111110000100010001000100001000",
        "8": "011101000101110100011000101110",
        "9": "011101000110001011110000101110",
        ".": "000000000000000000000110001100",
        ",": "000000000000000000000110001100",
        "-": "000000000001111000000000000000",
        "/": "000010001000100010001000100000",
        ":": "000000110001100000000110001100",
        "(": "001000100010000100001000000100",
        ")": "010000010000010000100010001000",
        "Q": "0111010001100011000110010011101",
    }


_FONT = _glyph_5x7()


def _blit_text(
    buf: bytearray,
    w: int,
    x: int,
    y: int,
    text: str,
    color: tuple[int, int, int],
    scale: int = 2,
) -> None:
    cx = x
    for ch in text:
        bits = _FONT.get(ch, _FONT[" "])
        # Some glyphs were stored as 5*7=35; pad/truncate.
        bits = (bits + "0" * 35)[:35]
        for row in range(7):
            for col in range(5):
                if bits[row * 5 + col] != "1":
                    continue
                for dy in range(scale):
                    for dx in range(scale):
                        px = cx + col * scale + dx
                        py = y + row * scale + dy
                        i = (py * w + px) * 3
                        if 0 <= i < len(buf) - 2:
                            buf[i] = color[0]
                            buf[i + 1] = color[1]
                            buf[i + 2] = color[2]
        cx += 6 * scale


def _write(path: Path, blob: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(blob)


def icon() -> bytes:
    w = h = 128
    buf = bytearray(w * h * 3)
    _fill(buf, w, h, _INK)
    _rect(buf, w, 16, 16, 112, 112, _TEAL_DK)
    _rect(buf, w, 24, 24, 104, 104, _INK)
    _blit_text(buf, w, 38, 52, "KERN", _TEAL, scale=3)
    return _png(w, h, bytes(buf))


def cover() -> bytes:
    w, h = 800, 280
    buf = bytearray(w * h * 3)
    _fill(buf, w, h, _INK)
    _rect(buf, w, 0, 0, 12, h, _TEAL)
    _blit_text(buf, w, 40, 70, "KERN INVENTORY AI", _TEAL, scale=4)
    _blit_text(buf, w, 40, 130, "Read-only reorder policy dry run", _TXT, scale=2)
    _blit_text(buf, w, 40, 170, "Forecast, safety stock, (s,Q) / (R,S) -- nothing written back", _MUTED, scale=2)
    _rect(buf, w, 40, 220, 220, 252, _WARM)
    _blit_text(buf, w, 56, 228, "FREE DRY RUN", _INK, scale=2)
    return _png(w, h, bytes(buf))


def screenshot_wizard() -> bytes:
    w, h = 960, 540
    buf = bytearray(w * h * 3)
    _fill(buf, w, h, (244, 246, 248))
    _rect(buf, w, 80, 60, 880, 480, _WHITE)
    _rect(buf, w, 80, 60, 880, 108, _PANEL)
    _blit_text(buf, w, 100, 74, "Kern Inventory Analysis", _TXT, scale=3)
    _blit_text(buf, w, 110, 140, "History days", _TEAL_DK, scale=2)
    _rect(buf, w, 110, 170, 280, 202, (235, 238, 242))
    _blit_text(buf, w, 120, 178, "90", _INK, scale=2)
    _blit_text(buf, w, 110, 230, "Confirmed sales only. No on-hand is sent.", _MUTED, scale=2)
    _blit_text(buf, w, 110, 260, "Nothing is written back to Odoo.", _MUTED, scale=2)
    _rect(buf, w, 110, 400, 310, 444, _TEAL)
    _blit_text(buf, w, 128, 412, "Run Analysis", _INK, scale=2)
    _rect(buf, w, 330, 400, 430, 444, (220, 224, 228))
    _blit_text(buf, w, 348, 412, "Close", _INK, scale=2)
    return _png(w, h, bytes(buf))


def screenshot_result() -> bytes:
    w, h = 960, 540
    buf = bytearray(w * h * 3)
    _fill(buf, w, h, (244, 246, 248))
    _rect(buf, w, 80, 60, 880, 480, _WHITE)
    _rect(buf, w, 80, 60, 880, 108, _PANEL)
    _blit_text(buf, w, 100, 74, "Summary", _TXT, scale=3)
    _blit_text(buf, w, 110, 140, "SKU     forecast   ROP    Q    safety", _TEAL_DK, scale=2)
    _rect(buf, w, 110, 168, 850, 170, _LINE)
    rows = (
        "A-100     42.1      85    120     28",
        "B-220     11.4      30     50     12",
        "C-018      3.2      14     24      6",
    )
    y = 190
    for row in rows:
        _blit_text(buf, w, 110, y, row, _INK, scale=2)
        y += 36
    _blit_text(buf, w, 110, 340, "Read-only dry run. Human reviews before any PO.", _MUTED, scale=2)
    return _png(w, h, bytes(buf))


def main() -> None:
    files = {
        "cover.png": cover(),
        "screenshot_wizard.png": screenshot_wizard(),
        "screenshot_result.png": screenshot_result(),
    }
    # icon.png is the hand-authored module icon; do not overwrite it.
    for series in ("17.0", "19.0"):
        dest = _REPO / "odoo_addon" / series / "kern_dry_run" / "static" / "description"
        for name, blob in files.items():
            _write(dest / name, blob)
            print(f"wrote {dest / name} ({len(blob)} bytes)")


if __name__ == "__main__":
    main()
