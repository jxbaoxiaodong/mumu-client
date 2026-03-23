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
