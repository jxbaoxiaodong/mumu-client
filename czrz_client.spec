# -*- mode: python ; coding: utf-8 -*-

block_cipher = None

datas = [
    ('templates', 'templates'),
    ('static', 'static'),
]

import os
if os.path.exists('cloudflare.exe'):
    datas.append(('cloudflare.exe', '.'))
elif os.path.exists('cloudflared'):
    datas.append(('cloudflared', '.'))

a = Analysis(
    ['client.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=[
        'flask', 'flask.json', 'werkzeug', 'werkzeug.security',
        'werkzeug.middleware.proxy_fix', 'jinja2', 'jinja2.ext',
        'requests', 'urllib3', 'PIL', 'PIL.Image',
        'dateutil', 'dateutil.parser', 'dateutil.relativedelta',
        'zhdate', 'zhdate.zh_date',
        'bs4', 'lxml',
        'pydub',
        'imageio', 'imageio_ffmpeg', 'imageio.plugins.ffmpeg',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'numpy', 'pandas', 'scipy', 'pytest', 'IPython', 'notebook'],
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
    name='CZRZClient',
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
)
