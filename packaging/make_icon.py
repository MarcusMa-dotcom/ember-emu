"""Draws the placeholder logo of Ember Emu: a small tree of glowing dots on a dark tile.

Needs nothing but the standard library. Run it from the project folder:

    python packaging/make_icon.py

It writes

* ``emberemu_app/static/logo.png`` and ``docs/images/logo.png`` (256 pixels),
* ``packaging/icon-1024.png``, the picture for the app, with the margin macOS
  icons have,
* ``packaging/Ember Emu.icns`` from it, on a Mac (with ``sips`` and ``iconutil``).

Replace the files to use another picture. For the app, replace
``icon-1024.png`` and run ``python packaging/make_icon.py --icns-only``.
"""

from __future__ import annotations

import math
import shutil
import struct
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path

DESIGN = 256                   # the picture is laid out on 256 x 256 units
TILE = (30, 34, 38)
LINE = (120, 130, 140)
EMBER = (240, 138, 36)
GLOW = (255, 196, 92)
RADIUS = 46                    # corner radius of the tile
APP_MARGIN = 0.1               # share of the canvas left free around the tile of the app icon
ICON_SIZES = (16, 32, 128, 256, 512)

# The tree: a root on the left, two nodes, five leaves. Coordinates in units.
ROOT = (54, 128)
NODES = [(128, 78), (128, 178)]
LEAVES = [(202, 44), (202, 86), (202, 128), (202, 170), (202, 212)]
EDGES = [(ROOT, NODES[0]), (ROOT, NODES[1]), (NODES[0], LEAVES[0]), (NODES[0], LEAVES[1]),
         (NODES[0], LEAVES[2]), (NODES[1], LEAVES[3]), (NODES[1], LEAVES[4])]
DOTS = [(ROOT, 20, EMBER), (NODES[0], 15, EMBER), (NODES[1], 15, EMBER)] + [
    (leaf, 10, GLOW if index in (1, 3) else EMBER) for index, leaf in enumerate(LEAVES)]


def _inside_tile(x: float, y: float) -> bool:
    """True if a point lies on the rounded square."""
    nearest_x = min(max(x, RADIUS), DESIGN - RADIUS)
    nearest_y = min(max(y, RADIUS), DESIGN - RADIUS)
    return (x - nearest_x) ** 2 + (y - nearest_y) ** 2 <= RADIUS ** 2


def _near_segment(x: float, y: float, start, end, width: float) -> bool:
    """True if a point is closer than ``width`` to the line from ``start`` to ``end``."""
    (ax, ay), (bx, by) = start, end
    length = (bx - ax) ** 2 + (by - ay) ** 2
    along = max(0.0, min(1.0, ((x - ax) * (bx - ax) + (y - ay) * (by - ay)) / length))
    return math.hypot(x - (ax + along * (bx - ax)), y - (ay + along * (by - ay))) <= width


def _sample(x: float, y: float) -> tuple[int, int, int, int]:
    """Colour and opacity of one point, given in units."""
    if not _inside_tile(x, y):
        return (0, 0, 0, 0)
    for (cx, cy), radius, colour in DOTS:
        if (x - cx) ** 2 + (y - cy) ** 2 <= radius ** 2:
            return (*colour, 255)
    for start, end in EDGES:
        if _near_segment(x, y, start, end, 3.0):
            return (*LINE, 255)
    return (*TILE, 255)


def render(size: int = 256, margin: float = 0.0, samples: int = 3) -> bytes:
    """The picture as PNG bytes.

    Args:
        size: Width and height in pixels.
        margin: Share of the canvas left transparent on every side.
        samples: Every pixel is the mean of ``samples`` x ``samples`` points.
    """
    rows = bytearray()
    scale = DESIGN / (size * (1 - 2 * margin))     # units per pixel
    offset = margin * size
    step = 1.0 / samples
    for py in range(size):
        rows.append(0)  # filter type of the row: none
        for px in range(size):
            total = [0, 0, 0, 0]
            for sy in range(samples):
                for sx in range(samples):
                    red, green, blue, alpha = _sample((px + (sx + 0.5) * step - offset) * scale,
                                                      (py + (sy + 0.5) * step - offset) * scale)
                    total[0] += red * alpha
                    total[1] += green * alpha
                    total[2] += blue * alpha
                    total[3] += alpha
            alpha = total[3] // (samples * samples)
            if total[3]:
                rows.extend((total[0] // total[3], total[1] // total[3], total[2] // total[3], alpha))
            else:
                rows.extend((0, 0, 0, 0))

    def chunk(kind: bytes, data: bytes) -> bytes:
        """One block of a PNG file."""
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    header = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)  # 8 bit RGBA
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress(bytes(rows), 9)) + chunk(b"IEND", b""))


def make_icns(source: Path, target: Path) -> bool:
    """Turn a 1024 pixel PNG into the icon file of a Mac app.

    Returns:
        False if the tools of macOS for it are missing or fail.
    """
    if shutil.which("sips") is None or shutil.which("iconutil") is None:
        return False
    with tempfile.TemporaryDirectory() as folder:
        iconset = Path(folder) / "icon.iconset"
        iconset.mkdir()
        for size in ICON_SIZES:
            for factor, suffix in ((1, ""), (2, "@2x")):
                name = iconset / f"icon_{size}x{size}{suffix}.png"
                done = subprocess.run(["sips", "-z", str(size * factor), str(size * factor), str(source),
                                       "--out", str(name)], capture_output=True, check=False)
                if done.returncode != 0:
                    return False
        done = subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(target)],
                              capture_output=True, check=False)
        return done.returncode == 0


def main() -> None:
    """Write the logo files and the app icon."""
    project = Path(__file__).resolve().parent.parent
    big = project / "packaging" / "icon-1024.png"
    if "--icns-only" not in sys.argv:
        logo = render()
        for target in (project / "emberemu_app" / "static" / "logo.png",
                       project / "docs" / "images" / "logo.png"):
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(logo)
            print(f"wrote {target.relative_to(project)} ({len(logo)} bytes)")
        big.write_bytes(render(1024, APP_MARGIN, samples=2))
        print(f"wrote {big.relative_to(project)}")
    icns = project / "packaging" / "Ember Emu.icns"
    if make_icns(big, icns):
        print(f"wrote {icns.relative_to(project)}")
    else:
        print("The app icon was not made: it needs sips and iconutil of macOS.")


if __name__ == "__main__":
    main()
