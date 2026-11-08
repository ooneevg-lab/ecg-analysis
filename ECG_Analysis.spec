# -*- mode: python ; coding: utf-8 -*-

import os
import sys
from pathlib import Path


block_cipher = None
project_dir = Path(SPECPATH)
python_dir = Path(sys.base_prefix)
tcl_dir = python_dir / 'tcl'
dll_dir = python_dir / 'DLLs'

# Help PyInstaller/tkinter find Tcl/Tk data in the base Python install.
os.environ['TCL_LIBRARY'] = str(tcl_dir / 'tcl8.6')
os.environ['TK_LIBRARY'] = str(tcl_dir / 'tk8.6')

datas = [
    (str(project_dir / 'app' / 'templates'), 'app/templates'),
    (str(project_dir / 'app' / 'static'), 'app/static'),
    (str(project_dir / 'models' / 'densenet121_multiclass.pt'), 'models'),
    (str(project_dir / 'utils'), 'utils'),
    (str(project_dir / 'i18n'), 'i18n'),
    (str(tcl_dir / 'tcl8.6'), '_tcl_data'),
    (str(tcl_dir / 'tk8.6'), '_tk_data'),
]

hiddenimports = [
    '_tkinter',
    'tkinter',
    'tkinter.commondialog',
    'tkinter.filedialog',
    'webview',
    'webview.platforms.winforms',
    'watchdog.events',
    'watchdog.observers',
    'pystray',
    'pystray._win32',
    'PIL',
    'PIL.Image',
    'PIL.ImageDraw',
    'PIL.ImageTk',
]

binaries = [
    (str(dll_dir / '_tkinter.pyd'), '.'),
    (str(dll_dir / 'tcl86t.dll'), '.'),
    (str(dll_dir / 'tk86t.dll'), '.'),
]

a = Analysis(
    [str(project_dir / 'desktop' / 'launcher.py')],
    pathex=[str(project_dir)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'fastai',
        'torchvision',
        'torchaudio',
        'triton',
        'tensorflow',
        'pandas',
        'numba',
        'llvmlite',
        'IPython',
        'jupyter',
        'notebook',
        'openpyxl',
        'sqlalchemy',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

tkinter_src = python_dir / 'Lib' / 'tkinter'
existing_modules = {entry[0] for entry in a.pure}
for py_file in tkinter_src.rglob('*.py'):
    rel = py_file.relative_to(tkinter_src)
    if rel.name == '__init__.py':
        module_name = 'tkinter'
        if rel.parent != Path('.'):
            module_name += '.' + '.'.join(rel.parent.parts)
    else:
        module_name = 'tkinter.' + '.'.join(rel.with_suffix('').parts)
    if module_name not in existing_modules:
        a.pure.append((module_name, str(py_file), 'PYMODULE'))

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='ECG_Analysis',
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
