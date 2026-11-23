# -*- mode: python ; coding: utf-8 -*-
"""把桌面窗口、后端和已经构建好的界面打成一个 exe。"""

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = Path(SPECPATH).resolve().parent
sys.path.insert(0, str(ROOT / "backend"))

webview_datas, webview_bins, webview_hidden = collect_all("webview")
pythonnet_datas, pythonnet_bins, pythonnet_hidden = collect_all("pythonnet")

hidden = []
hidden += collect_submodules("app")
hidden += collect_submodules("uvicorn")
hidden += webview_hidden
hidden += pythonnet_hidden
hidden += [
    "clr",
    "pymysql",
    "cryptography",
    "pydantic",
    "pydantic_settings",
    "multipart",
    "docx",
    "bs4",
    "pypdf",
    "httpx",
    "anyio",
    "sqlalchemy.dialects.sqlite",
    "sqlalchemy.dialects.mysql",
    "sqlalchemy.dialects.mysql.pymysql",
]

datas = [
    (str(ROOT / "frontend" / "dist"), "frontend/dist"),
    (str(ROOT / "backend" / "skills"), "backend/skills"),
    (str(ROOT / "backend" / "mcp_servers"), "backend/mcp_servers"),
    (str(ROOT / "desktop" / "duck.ico"), "desktop"),
    (str(ROOT / "desktop" / "duck.png"), "desktop"),
]
datas += webview_datas
datas += pythonnet_datas

a = Analysis(
    [str(ROOT / "desktop" / "launch.py")],
    pathex=[str(ROOT / "backend")],
    binaries=webview_bins + pythonnet_bins,
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "tkinter", "_pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="Duck Harness",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    icon=str(ROOT / "desktop" / "duck.ico"),
)
