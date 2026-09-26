# build_client.spec
#
# PyInstaller spec file for packaging messenger_client.py into a single
# portable .exe that can run directly from the USB drive with no
# installation on the host machine.
#
# Usage (run from the build/ directory, on a WINDOWS machine --
# PyInstaller builds platform-specific executables, so this must be
# run on Windows to produce a Windows .exe):
#
#   pip install -r ../client/requirements.txt
#   pip install pyinstaller
#   pyinstaller build_client.spec
#
# Output: dist/USBMessenger.exe
# Copy that single file onto the USB drive alongside identity.key and
# face_template.dat (which get created the first time it's run).

# -*- mode: python ; coding: utf-8 -*-

import cv2
import os

# SPECPATH is provided automatically by PyInstaller -- it's the directory
# containing this .spec file, so paths work regardless of where the
# `pyinstaller` command is invoked from.
CLIENT_DIR = os.path.join(SPECPATH, "..", "client")

# OpenCV's face module needs its Haar cascade XML data file bundled in.
# Some opencv-contrib-python builds (confirmed on 5.0.0.93) ship WITHOUT
# this file in their own package data folder, so we prefer a local copy
# placed in client/ (see README for the download instructions) and only
# fall back to the package's own copy if that's actually present.
local_cascade = os.path.join(CLIENT_DIR, "haarcascade_frontalface_default.xml")
package_cascade_dir = cv2.data.haarcascades

cascade_files = []
if os.path.exists(local_cascade):
    cascade_files.append((local_cascade, "cv2/data"))
elif os.path.exists(package_cascade_dir):
    cascade_files = [
        (os.path.join(package_cascade_dir, f), "cv2/data")
        for f in os.listdir(package_cascade_dir)
        if f.endswith(".xml")
    ]

if not cascade_files:
    raise FileNotFoundError(
        "No haarcascade_frontalface_default.xml found to bundle. Download it into "
        "the client/ folder first -- see README.md for the exact steps."
    )

a = Analysis(
    [os.path.join(CLIENT_DIR, "messenger_client.py")],
    pathex=[CLIENT_DIR],
    binaries=[],
    datas=cascade_files,
    hiddenimports=[
        "cv2",
        "cryptography",
        "websockets",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="USBMessenger",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,  # keep the console window -- this is a CLI app for now
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    onefile=True,
)
