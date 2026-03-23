#!/bin/bash
# Build script for CZRZ Client (Linux/macOS)
# No Chinese or emoji characters

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "=========================================="
echo "CZRZ Client Build Script"
echo "Platform: $(uname -s) $(uname -m)"
echo "=========================================="

# Check pyinstaller
if ! command -v pyinstaller &> /dev/null; then
    echo "Error: pyinstaller not found"
    echo "Please install: pip install pyinstaller"
    exit 1
fi

# Clean old build
echo "[1/5] Cleaning old build..."
rm -rf build/ dist/ *.spec

# Create spec file
echo "[2/5] Creating spec file..."
cat > czrz_client.spec << 'SPEC_EOF'
# -*- mode: python ; coding: utf-8 -*-
import sys
from pathlib import Path

block_cipher = None

# Detect platform
is_windows = sys.platform == 'win32'
is_darwin = sys.platform == 'darwin'
is_linux = sys.platform.startswith('linux')

# Executable name
if is_windows:
    exe_name = 'CZRZClient'
    cloudflared_name = 'cloudflare.exe'
else:
    exe_name = 'CZRZClient'
    cloudflared_name = 'cloudflared'

# Find cloudflared executable
cloudflared_paths = [
    Path('cloudflared'),
    Path('cloudflare.exe'),
]
cloudflared_bin = None
for p in cloudflared_paths:
    if p.exists():
        cloudflared_bin = str(p)
        break

# Data files
datas = [
    ('templates', 'templates'),
    ('static', 'static'),
]

if cloudflared_bin:
    datas.append((cloudflared_bin, '.'))

a = Analysis(
    ['client_public_final.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=[
        'flask',
        'flask.json',
        'werkzeug',
        'werkzeug.security',
        'werkzeug.middleware.proxy_fix',
        'jinja2',
        'jinja2.ext',
        'requests',
        'urllib3',
        'PIL',
        'PIL.Image',
        'dateutil',
        'dateutil.parser',
        'dateutil.relativedelta',
        'zhdate',
        'zhdate.zh_date',
        'bs4',
        'lxml',
        'pydub',
        'imageio_ffmpeg',
        'imageio_ffmpeg.binaries',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter',
        'matplotlib',
        'numpy',
        'pandas',
        'scipy',
        'pytest',
        'IPython',
        'notebook',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name=exe_name,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='static/images/icon.ico' if Path('static/images/icon.ico').exists() else None,
)
SPEC_EOF

# Build
echo "[3/5] Building executable..."
pyinstaller czrz_client.spec --clean

# Check result
echo "[4/5] Checking build result..."
if [ -f "dist/CZRZClient" ] || [ -f "dist/CZRZClient.exe" ]; then
    echo "Build successful!"
    ls -la dist/
else
    echo "Error: Build failed - executable not found"
    exit 1
fi

# Create release package
echo "[5/5] Creating release package..."
RELEASE_DIR="dist/CZRZClient_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$RELEASE_DIR"
cp -r dist/CZRZClient* "$RELEASE_DIR/" 2>/dev/null || cp dist/CZRZClient "$RELEASE_DIR/"

# Copy cloudflared if not included
if [ -f "cloudflared" ] && [ ! -f "$RELEASE_DIR/cloudflared" ]; then
    cp cloudflared "$RELEASE_DIR/"
    chmod +x "$RELEASE_DIR/cloudflared"
fi

# Create README
cat > "$RELEASE_DIR/README.txt" << 'README_EOF'
CZRZ Client - Growth Record System
==================================

Usage:
1. Run CZRZClient (Linux/macOS) or CZRZClient.exe (Windows)
2. Open browser and access http://localhost:3000
3. Follow the setup wizard

Requirements:
- For public access, ensure cloudflared is in the same directory

Support:
- Check logs in ~/Documents/CZRZ/logs/

README_EOF

echo "=========================================="
echo "Build complete!"
echo "Output: $RELEASE_DIR"
echo "=========================================="