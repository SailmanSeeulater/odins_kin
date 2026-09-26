# -*- mode: python ; coding: utf-8 -*-
# Build with: pyinstaller Odins_Kin.spec


a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('templates', 'templates'),
        ('static', 'static'),
    ],
    # server is imported lazily when the Dashboard link is clicked
    hiddenimports=['server'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Pillow's optional viewers would otherwise drag in Qt, numpy and IPython (~80 MB)
    excludes=[
        'numpy', 'matplotlib', 'IPython', 'ipykernel', 'jupyter_client', 'nbformat', 'zmq',
        'tornado', 'jedi', 'prompt_toolkit', 'pygments', 'rich', 'markdown_it',
        'PyQt5', 'PyQt6', 'PySide2', 'PySide6', 'setuptools', 'pkg_resources', 'distutils',
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
    name='Odins_Kin',
    icon='assets/odins_kin.ico',
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
)
