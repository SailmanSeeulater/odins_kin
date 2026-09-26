"""Human names and real icons for the processes Odin's Kin sees.

Names come from each executable's FileDescription ("Code.exe" -> "Visual Studio Code").
Icons are pulled from the executable and cached as PNGs, so the web dashboard can
show them too. Both degrade to a tidy fallback off Windows or without pywin32.
"""

import json
import re
import sys
import threading
from pathlib import Path

from database import data_dir

ICON_SIZE = 64
UNKNOWN_NAME = "Unidentified"

# Per-app hues sampled from the Instagram spectrum, ordered so neighbours in a
# ranking contrast (warm, cool, warm...). Apps past the palette share "other".
APP_COLORS = ["#D62976", "#FA7E1E", "#4F5BD5", "#962FBF", "#F2B01E", "#E8505B"]

_lock = threading.Lock()


def icons_dir() -> Path:
    folder = data_dir() / "icons"
    folder.mkdir(exist_ok=True)
    return folder


def _catalog_path() -> Path:
    return data_dir() / "apps.json"


def _load_catalog() -> dict:
    try:
        return json.loads(_catalog_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_catalog(catalog: dict):
    tmp = _catalog_path().with_suffix(".tmp")
    tmp.write_text(json.dumps(catalog, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(_catalog_path())


def icon_key(process: str) -> str:
    return re.sub(r"[^a-z0-9._-]", "_", process.lower()) or "unknown"


def fallback_name(process: str) -> str:
    if not process or process == "Unknown":
        return UNKNOWN_NAME
    stem = re.sub(r"\.exe$", "", process, flags=re.I)
    return stem[:1].upper() + stem[1:]


def _file_description(exe: str) -> str:
    try:
        import win32api

        langs = win32api.GetFileVersionInfo(exe, "\\VarFileInfo\\Translation")
        for lang, codepage in langs or [(0x0409, 0x04B0)]:
            desc = win32api.GetFileVersionInfo(
                exe, f"\\StringFileInfo\\{lang:04x}{codepage:04x}\\FileDescription"
            )
            if desc and desc.strip():
                return desc.strip()
    except Exception:
        pass
    return ""


def _extract_icon(exe: str, size: int = ICON_SIZE):
    """The executable's icon as a Pillow RGBA image, or None."""
    if sys.platform != "win32" or not exe:
        return None
    import ctypes
    from ctypes import wintypes

    from PIL import Image

    user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32

    class ICONINFO(ctypes.Structure):
        _fields_ = [
            ("fIcon", wintypes.BOOL),
            ("xHotspot", wintypes.DWORD),
            ("yHotspot", wintypes.DWORD),
            ("hbmMask", wintypes.HBITMAP),
            ("hbmColor", wintypes.HBITMAP),
        ]

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", wintypes.DWORD),
            ("biWidth", wintypes.LONG),
            ("biHeight", wintypes.LONG),
            ("biPlanes", wintypes.WORD),
            ("biBitCount", wintypes.WORD),
            ("biCompression", wintypes.DWORD),
            ("biSizeImage", wintypes.DWORD),
            ("biXPelsPerMeter", wintypes.LONG),
            ("biYPelsPerMeter", wintypes.LONG),
            ("biClrUsed", wintypes.DWORD),
            ("biClrImportant", wintypes.DWORD),
        ]

    user32.PrivateExtractIconsW.argtypes = [
        wintypes.LPCWSTR, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        ctypes.POINTER(wintypes.HICON), ctypes.POINTER(wintypes.UINT), wintypes.UINT, wintypes.UINT,
    ]
    user32.GetIconInfo.argtypes = [wintypes.HICON, ctypes.POINTER(ICONINFO)]
    user32.DestroyIcon.argtypes = [wintypes.HICON]
    user32.GetDC.argtypes = [wintypes.HWND]
    user32.GetDC.restype = wintypes.HDC
    user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    gdi32.GetDIBits.argtypes = [
        wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT,
        ctypes.c_void_p, ctypes.POINTER(BITMAPINFOHEADER), wintypes.UINT,
    ]
    gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]

    hicon = wintypes.HICON()
    icon_id = wintypes.UINT()
    if user32.PrivateExtractIconsW(exe, 0, size, size, ctypes.byref(hicon), ctypes.byref(icon_id), 1, 0) < 1:
        return None
    if not hicon:
        return None

    info = ICONINFO()
    hdc = user32.GetDC(None)
    try:
        if not user32.GetIconInfo(hicon, ctypes.byref(info)) or not info.hbmColor:
            return None

        def bits(bitmap):
            header = BITMAPINFOHEADER(
                biSize=ctypes.sizeof(BITMAPINFOHEADER), biWidth=size, biHeight=-size,
                biPlanes=1, biBitCount=32, biCompression=0,
            )
            buf = ctypes.create_string_buffer(size * size * 4)
            if not gdi32.GetDIBits(hdc, bitmap, 0, size, buf, ctypes.byref(header), 0):
                return None
            return buf.raw

        color = bits(info.hbmColor)
        if color is None:
            return None
        img = Image.frombuffer("RGBA", (size, size), color, "raw", "BGRA", 0, 1).copy()
        if img.getextrema()[3][1] == 0:
            # Legacy icon without alpha: the AND mask marks transparent pixels white
            mask = bits(info.hbmMask) if info.hbmMask else None
            if mask:
                alpha = Image.frombuffer("RGBA", (size, size), mask, "raw", "BGRA", 0, 1)
                img.putalpha(alpha.convert("L").point(lambda v: 0 if v > 127 else 255))
            else:
                img.putalpha(255)
        return img
    finally:
        if info.hbmColor:
            gdi32.DeleteObject(info.hbmColor)
        if info.hbmMask:
            gdi32.DeleteObject(info.hbmMask)
        user32.DestroyIcon(hicon)
        user32.ReleaseDC(None, hdc)


def resolve(process: str, exe: str = "") -> dict:
    """{name, icon} for a process, cached in apps.json. icon is a filename in icons_dir() or None."""
    key = icon_key(process)
    with _lock:
        catalog = _load_catalog()
        entry = catalog.get(key)
        # Re-resolve when we learn an exe path for an app we only knew by name
        if entry and (entry.get("exe") or not exe):
            return entry

        name = (_file_description(exe) if exe else "") or fallback_name(process)
        icon_file = None
        try:
            img = _extract_icon(exe)
            if img is not None:
                icon_file = f"{key}.png"
                img.save(icons_dir() / icon_file)
        except Exception:
            icon_file = None

        entry = {"process": process, "name": name, "exe": exe, "icon": icon_file}
        catalog[key] = entry
        try:
            _save_catalog(catalog)
        except OSError:
            pass
        return entry


def icon_image(process: str, exe: str = ""):
    """Cached icon as a Pillow image, or None."""
    entry = resolve(process, exe)
    if not entry.get("icon"):
        return None
    try:
        from PIL import Image

        return Image.open(icons_dir() / entry["icon"]).convert("RGBA")
    except Exception:
        return None


def color_map(view_ranked, global_ranked) -> dict:
    """App colors that stay put across views.

    The all-time top apps own a hue each. Apps in this view without one borrow the
    hues nobody in the view is using, in rank order. Anything left over is 'other'
    (absent from the map). Mirrored by colorMap() in templates/index.html."""
    owned = {proc: APP_COLORS[i] for i, proc in enumerate(global_ranked[: len(APP_COLORS)])}
    colors = {proc: owned[proc] for proc in view_ranked if proc in owned}
    free = [c for c in APP_COLORS if c not in colors.values()]
    for proc in view_ranked:
        if proc not in colors and free:
            colors[proc] = free.pop(0)
    return colors
