# -*- mode: python ; coding: utf-8 -*-

"""PyInstaller spec for the standalone update replacement helper.

Build this first, then include the resulting `update_installer_helper.exe`
in the main application package (see the main .spec files).
"""

import os

PROJECT_ROOT = os.path.dirname(os.path.abspath(SPECPATH))
SRC_DIR = os.path.join(PROJECT_ROOT, "src")
PACKAGING_DIR = os.path.join(PROJECT_ROOT, "packaging")

a = Analysis(
    [os.path.join(SRC_DIR, "doubao2api", "update_installer_helper.py")],
    pathex=[SRC_DIR],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
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
    name="update_installer_helper",
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
    icon=[os.path.join(PACKAGING_DIR, "app-icon.ico")],
)
