# =============================================================================
#  noise_floor_meter.spec — PyInstaller build spec
#  Génère un .exe Windows autonome (onefile)
# =============================================================================

import sys
from pathlib import Path

block_cipher = None

a = Analysis(
    ['src/noise_floor_meter.py'],
    pathex=['.'],
    binaries=[],
    datas=[
        ('assets/nfm.ico', '.'),
    ],
    hiddenimports=[
        'scipy.signal',
        'scipy.stats',
        'scipy._lib.messagestream',
        'scipy.special._ufuncs_cxx',
        'numpy',
        'numpy.core._methods',
        'numpy.lib.format',
        'matplotlib',
        'matplotlib.backends.backend_tkagg',
        'matplotlib.backends._backend_tk',
        'sounddevice',
        '_sounddevice_data',
        'tkinter',
        'tkinter.ttk',
        'tkinter.messagebox',
        'tkinter.filedialog',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'PyQt5', 'PyQt6', 'PySide2', 'PySide6',
        'wx', 'IPython', 'jupyter',
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
    name='NoisFloorMeter',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,          # Pas de fenêtre console
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='assets/nfm.ico',
    version_file=None,
    uac_admin=False,
)
