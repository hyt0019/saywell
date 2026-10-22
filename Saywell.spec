# Build with: .venv\Scripts\python.exe -m PyInstaller --noconfirm Saywell.spec
from PyInstaller.utils.hooks import collect_all, collect_data_files
import os
from pathlib import Path
import sys

# Avoid collecting unrelated ICU/UCRT libraries from tools elsewhere on PATH.
# Qt on supported Windows versions uses the operating system's ICU API.
system_root = Path(os.environ.get('SystemRoot', 'C:/Windows'))
os.environ['PATH'] = os.pathsep.join(str(p) for p in (
    Path(sys.executable).parent, Path(sys.base_prefix), system_root / 'System32', system_root,
))

sherpa_data, sherpa_binaries, sherpa_hidden = collect_all('sherpa_onnx')
a = Analysis(
    ['scripts/run_app.py'],
    pathex=['src'],
    binaries=sherpa_binaries,
    datas=sherpa_data + collect_data_files('pypinyin') + [('src/saywell/assets', 'saywell/assets')],
    hiddenimports=sherpa_hidden + ['saywell.cli', 'keyring.backends.Windows', 'win32crypt'],
    excludes=['tkinter', 'pytest', 'ruff'],
)
# These libraries are supplied by Windows 10/11 and must not be shadowed.
a.binaries = [entry for entry in a.binaries if
              Path(entry[0]).name.lower() not in {'icuuc.dll', 'icuin.dll', 'ucrtbase.dll'}
              and not Path(entry[0]).name.lower().startswith('api-ms-win-')]
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='Saywell',
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
          console=False, icon='assets/app.ico')
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='Saywell')
