# -*- mode: python ; coding: utf-8 -*-
# Профиль сборки PyInstaller (папочный режим, без окна консоли).
# AMIRS_CONSOLE=1 — отладочная сборка С консолью (видны трассировки ошибок запуска)
# AMIRS_EMBED_MODELS=1 — дополнительно вшить paddle_models внутрь сборки
# (по умолчанию не нужно: программа ищет модели рядом с EXE)
import os
from PyInstaller.utils.hooks import collect_all

datas = []
binaries = []
hiddenimports = [
    'pypdf', 'pypdfium2', 'pypdfium2_raw',
    'PIL', 'PIL.Image', 'PIL.ImageSequence',
    'paddle', 'paddleocr', 'scipy.special.cython_special'
]

skipped = []
for pkg in ('pypdfium2', 'pypdfium2_raw', 'paddleocr', 'paddle', 'pyclipper', 'shapely', 'skimage', 'imgaug', 'lmdb'):
    try:
        tmp_ret = collect_all(pkg)
        datas += tmp_ret[0]
        binaries += tmp_ret[1]
        hiddenimports += tmp_ret[2]
    except Exception as exc:  # пакет не установлен — сборка продолжается, но об этом сообщается
        skipped.append(pkg)
        print(f"[spec] ВНИМАНИЕ: пакет {pkg} не включён в сборку ({type(exc).__name__}: {exc})")
if skipped:
    print("[spec] Не включены: " + ", ".join(skipped))

if os.environ.get("AMIRS_EMBED_MODELS") == "1" and os.path.isdir('paddle_models'):
    datas.append(('paddle_models', 'paddle_models'))

use_console = os.environ.get("AMIRS_CONSOLE") == "1"

a = Analysis(
    ['amirs_converter.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
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
    [],
    exclude_binaries=True,
    name='Конвертер_АМИРС',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # UPX портит библиотеки Paddle и вызывает ложные срабатывания антивирусов
    console=use_console,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='Конвертер_АМИРС',
)
