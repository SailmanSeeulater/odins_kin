"""Render assets/odins_kin.ico (the window and .exe icon) from graphics.app_mark."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import graphics  # noqa: E402

if __name__ == "__main__":
    out = ROOT / "assets" / "odins_kin.ico"
    out.parent.mkdir(exist_ok=True)
    sizes = [16, 24, 32, 48, 64, 128, 256]
    graphics.app_mark(256).save(out, sizes=[(s, s) for s in sizes])
    print(out)
