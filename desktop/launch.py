"""打开桌面窗口。如果本机还没有服务，就先把后端拉起来。"""

import ctypes
import json
import subprocess
import sys
import threading
import time
import urllib.request
from ctypes import wintypes
from pathlib import Path

import webview

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
URL = "http://127.0.0.1:8787"
GEOMETRY_FILE = Path.home() / "AppData" / "Local" / "DuckHarness" / "window.json"
_geometry = {"x": None, "y": None, "width": 1280, "height": 840, "maximized": False, "sidebarWidth": 300, "sidebarOpen": True}
_save_timer = None


def healthy() -> bool:
    try:
        with urllib.request.urlopen(URL + "/api/health", timeout=1) as response:
            return response.status == 200
    except Exception:
        return False


def ensure_server():
    if healthy():
        return None
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8787"],
        cwd=str(BACKEND),
        creationflags=flags,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for _ in range(60):
        if healthy():
            return process
        time.sleep(0.25)
    return process


_window = None
_maximized = False


class DesktopApi:
    def minimize(self):
        _window.minimize()

    def maximize(self):
        global _maximized
        if _maximized:
            _window.restore()
            _maximized = False
        else:
            _window.maximize()
            _maximized = True

    def close(self):
        _window.destroy()

    def drag(self):
        return None

    def resize_by(self, drag_id, edge, dx, dy):
        _queue_resize(drag_id, edge, dx, dy)

    def layout(self):
        return {"sidebarWidth": int(_geometry.get("sidebarWidth") or 300), "sidebarOpen": bool(_geometry.get("sidebarOpen", True))}

    def save_layout(self, width, opened):
        try:
            _geometry["sidebarWidth"] = min(520, max(220, int(float(width))))
        except (TypeError, ValueError):
            return None
        if isinstance(opened, str):
            _geometry["sidebarOpen"] = opened.strip().lower() not in {"0", "false", ""}
        else:
            _geometry["sidebarOpen"] = bool(opened)
        _schedule_save()
        return self.layout()


def _icon_file() -> Path:
    return Path(__file__).resolve().parent / "duck.ico"


def _apply_icon():
    try:
        hwnd = int(_window.native.Handle)
        user32 = ctypes.windll.user32
        user32.SetClassLongPtrW.restype = ctypes.c_void_p
        user32.SetClassLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
        big = user32.LoadImageW(None, str(_icon_file()), 1, 48, 48, 0x00000010)
        small = user32.LoadImageW(None, str(_icon_file()), 1, 16, 16, 0x00000010)
        if big:
            user32.SendMessageW(hwnd, 0x0080, 1, big)
            user32.SetClassLongPtrW(hwnd, -14, big)
        if small:
            user32.SendMessageW(hwnd, 0x0080, 0, small)
            user32.SetClassLongPtrW(hwnd, -34, small)
    except Exception:
        return


_resize_lock = threading.Lock()
_resize_origin = None
_resize_pending = None
_resize_busy = False
_user32 = ctypes.WinDLL("user32", use_last_error=True)
_user32.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.RECT)]
_user32.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint]
_user32.IsZoomed.argtypes = [ctypes.c_void_p]
_user32.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]


def _hwnd():
    return int(_window.native.Handle.ToInt32())


def _window_box(hwnd):
    rect = wintypes.RECT()
    _user32.GetWindowRect(hwnd, ctypes.byref(rect))
    return rect.left, rect.top, rect.right, rect.bottom


def _queue_resize(drag_id, edge, dx, dy):
    global _resize_pending, _resize_busy
    if _window is None:
        return
    with _resize_lock:
        _resize_pending = (int(drag_id), str(edge or ""), float(dx), float(dy))
        if _resize_busy:
            return
        _resize_busy = True
    while True:
        with _resize_lock:
            pending = _resize_pending
            _resize_pending = None
        if pending is None:
            with _resize_lock:
                if _resize_pending is None:
                    _resize_busy = False
                    return
            continue
        _apply_resize(*pending)


def _load_geometry():
    try:
        data = json.loads(GEOMETRY_FILE.read_text(encoding="utf-8"))
    except Exception:
        return
    for key in ("x", "y", "width", "height"):
        if data.get(key) is None:
            continue
        _geometry[key] = int(data[key])
    _geometry["maximized"] = bool(data.get("maximized"))
    _geometry["width"] = min(4000, max(960, _geometry["width"]))
    _geometry["height"] = min(3000, max(640, _geometry["height"]))
    if _geometry["x"] is not None and not -400 <= _geometry["x"] <= 6000:
        _geometry["x"] = None
    if _geometry["y"] is not None and not -400 <= _geometry["y"] <= 4000:
        _geometry["y"] = None
    if data.get("sidebarWidth") is not None:
        _geometry["sidebarWidth"] = min(520, max(220, int(data["sidebarWidth"])))
    if "sidebarOpen" in data:
        _geometry["sidebarOpen"] = bool(data.get("sidebarOpen"))


def _schedule_save():
    global _save_timer
    if _save_timer:
        _save_timer.cancel()
    _save_timer = threading.Timer(0.4, _write_geometry)
    _save_timer.daemon = True
    _save_timer.start()


def _write_geometry():
    try:
        GEOMETRY_FILE.parent.mkdir(parents=True, exist_ok=True)
        GEOMETRY_FILE.write_text(json.dumps(_geometry), encoding="utf-8")
    except Exception:
        return


def _on_resized(width, height):
    zoomed = False
    try:
        zoomed = bool(_user32.IsZoomed(_hwnd()))
    except Exception:
        zoomed = False
    _geometry["maximized"] = zoomed
    if not zoomed:
        _geometry["width"] = min(4000, max(960, int(width)))
        _geometry["height"] = min(3000, max(640, int(height)))
    _schedule_save()


def _on_moved(x, y):
    zoomed = False
    try:
        zoomed = bool(_user32.IsZoomed(_hwnd()))
    except Exception:
        zoomed = False
    _geometry["maximized"] = zoomed
    if not zoomed:
        _geometry["x"] = int(x)
        _geometry["y"] = int(y)
    _schedule_save()


def _on_shown():
    _apply_icon()
    if _geometry.get("maximized"):
        _window.maximize()


def _apply_resize(drag_id, edge, dx, dy):
    global _resize_origin
    hwnd = _hwnd()
    scale = float(getattr(_window.native, "_scale", 1) or 1)
    with _resize_lock:
        if _resize_origin is None or _resize_origin["id"] != drag_id:
            if _user32.IsZoomed(hwnd):
                _user32.ShowWindow(hwnd, 9)
            _resize_origin = {"id": drag_id, "box": _window_box(hwnd)}
        left, top, right, bottom = _resize_origin["box"]
    px = int(round(dx * scale))
    py = int(round(dy * scale))
    min_w = int(round(960 * scale))
    min_h = int(round(640 * scale))
    if "w" in edge:
        left = min(left + px, right - min_w)
    if "e" in edge:
        right = max(right + px, left + min_w)
    if "n" in edge:
        top = min(top + py, bottom - min_h)
    if "s" in edge:
        bottom = max(bottom + py, top + min_h)
    width = max(min_w, right - left)
    height = max(min_h, bottom - top)
    _user32.SetWindowPos(hwnd, None, left, top, width, height, 0x0014)
    scale = float(getattr(_window.native, "_scale", 1) or 1) or 1
    _geometry["x"] = int(round(left / scale))
    _geometry["y"] = int(round(top / scale))
    _geometry["width"] = max(960, int(round(width / scale)))
    _geometry["height"] = max(640, int(round(height / scale)))
    _geometry["maximized"] = False
    _schedule_save()


def main():
    global _window
    icon = str(_icon_file())
    _load_geometry()
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("DuckHarness.Agent")
    webview.settings["ALLOW_DOWNLOADS"] = True
    ensure_server()
    placed = _geometry["x"] is not None and _geometry["y"] is not None
    _window = webview.create_window(
        "duck-harness",
        URL,
        js_api=DesktopApi(),
        width=_geometry["width"],
        height=_geometry["height"],
        x=_geometry["x"] if placed else None,
        y=_geometry["y"] if placed else None,
        min_size=(960, 640),
        maximized=bool(_geometry["maximized"]),
        frameless=True,
        easy_drag=False,
        resizable=True,
        text_select=True,
        background_color="#FFFFFF",
    )
    _window.events.resized += _on_resized
    _window.events.moved += _on_moved
    _window.events.shown += _on_shown
    webview.start(icon=icon)


if __name__ == "__main__":
    main()
