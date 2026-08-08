# -*- mode: python ; coding: utf-8 -*-


block_cipher = None


a = Analysis(
    ['server_main.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('web/web_templates', 'web/web_templates'),
        ('tools/SumatraPDF.exe', 'tools'),
    ],
    hiddenimports=[
        'pystray._win32',
        'PIL._tkinter_finder',
        'win32print',
        'win32gui',
        'win32con',
        'win32api',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'torch', 'torchvision', 'torchaudio',
        'pandas', 'numpy.random._examples',
        'scipy', 'sklearn', 'matplotlib',
        'PyQt5', 'PyQt6', 'PySide2', 'PySide6',
        'IPython', 'jupyter', 'notebook',
        'onnxruntime', 'tensorflow', 'keras',
        'sqlalchemy', 'pydantic',
        'rich', 'pygments',
        'fsspec',
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
    name='云印宝服务端',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
