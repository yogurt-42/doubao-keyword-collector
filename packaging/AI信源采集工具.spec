# -*- mode: python ; coding: utf-8 -*-

import os

# SPECPATH is the directory containing this .spec file (packaging/).
# Its parent is the project root.
PROJECT_ROOT = os.path.dirname(os.path.abspath(SPECPATH))
SRC_DIR = os.path.join(PROJECT_ROOT, "src")
PACKAGING_DIR = os.path.join(PROJECT_ROOT, "packaging")

a = Analysis(
    [os.path.join(SRC_DIR, "doubao2api", "windows_entry.py")],
    pathex=[SRC_DIR],
    binaries=[],
    datas=[
        (os.path.join(SRC_DIR, "doubao2api", "assets"), "doubao2api\\assets"),
        (os.path.join(SRC_DIR, "doubao2api", "static"), "doubao2api\\static"),
    ],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "doubao2api.server",
        "fastapi",
        "starlette",
        "uvicorn",
        "pydantic",
        "rich",
        "playwright",
        "lxml",
        "pandas",
        "charset_normalizer",
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="doubao-keyword-collector",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version=os.path.join(PACKAGING_DIR, "windows_version_info.txt"),
    icon=[os.path.join(PACKAGING_DIR, "app-icon.ico")],
)
