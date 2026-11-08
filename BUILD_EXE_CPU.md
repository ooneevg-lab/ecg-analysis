# Сборка ECG_Analysis.exe через PyInstaller

Инструкция для Windows-сборки без GPU. Итоговый файл создается как один `exe` с консолью.

## 1. Откройте PowerShell в папке GUI

```powershell
cd "C:\Users\oonee\OneDrive\Desktop\ПАПКИ\mimoGUI\финалочкаСВЫБОРОМПАПКИ\ДОРЕАНАЛИЗА222ПРЕДВАРИТЕЛЬНЫЙИТОГ — копия — копия\GUI"
```

## 2. Создайте CPU-only окружение

```powershell
python -m venv .venv-build-cpu
.\.venv-build-cpu\Scripts\python -m pip install --upgrade pip
.\.venv-build-cpu\Scripts\pip install pyinstaller flask flask-cors numpy scipy reportlab matplotlib lxml pywebview watchdog
.\.venv-build-cpu\Scripts\pip install torch --index-url https://download.pytorch.org/whl/cpu
```

Проверки:

```powershell
.\.venv-build-cpu\Scripts\python -c "import torch; print(torch.__version__); print(torch.version.cuda); print(torch.cuda.is_available())"
.\.venv-build-cpu\Scripts\python -c "import watchdog; import watchdog.observers; print('watchdog ok')"
.\.venv-build-cpu\Scripts\python -c "import tkinter; from tkinter import filedialog; print('tkinter import ok')"
```

Для CPU-only Torch ожидаемо: `torch.version.cuda` = `None`, `cuda.is_available()` = `False`.

## 3. Очистите старые артефакты

```powershell
Get-Process -Name ECG_Analysis -ErrorAction SilentlyContinue | Stop-Process -Force

if (Test-Path .\build) { Remove-Item .\build -Recurse -Force }
if (Test-Path .\dist) { Remove-Item .\dist -Recurse -Force }
if (Test-Path .\.runtime-data) { Remove-Item .\.runtime-data -Recurse -Force }
if (Test-Path .\.runtime-data-exe) { Remove-Item .\.runtime-data-exe -Recurse -Force }

$pyinstallerCache = Join-Path $env:LOCALAPPDATA "pyinstaller"
if (Test-Path $pyinstallerCache) { Remove-Item $pyinstallerCache -Recurse -Force }
```

## 4. Соберите exe

```powershell
.\.venv-build-cpu\Scripts\pyinstaller --noconfirm --clean ECG_Analysis.spec
```

Готовый файл:

```text
dist\ECG_Analysis.exe
```

## 5. Где сохраняются XML

В dev-режиме по умолчанию:

```text
<папка GUI>\uploads
```

В собранном exe по умолчанию:

```text
%LOCALAPPDATA%\ECG_Analysis\uploads
```

Если пользователь выбрал папку через интерфейс, XML пишутся в выбранную папку. Выбор сохраняется в `.session_state.json` внутри текущей рабочей папки данных.

Можно принудительно задать папку перед запуском:

```powershell
$env:ECG_UPLOAD_FOLDER="D:\ECG_TEMP"
.\dist\ECG_Analysis.exe
```

Временная распаковка PyInstaller (`%TEMP%\_MEIxxxxx`) не является папкой хранения XML.
