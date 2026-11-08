# Изменения: настраиваемая папка для временных файлов

## Что было сделано

Реализована возможность выбора произвольной папки на диске для хранения временных XML-файлов (вместо жёстко заданной `uploads/` в папке проекта). Добавлен UI для просмотра статистики папки (размер, количество файлов) и её очистки. Исправлены пути для корректной сборки PyInstaller.

---

## Изменённые файлы

### 1. `app/config.py`

**Было:**
- `UPLOAD_FOLDER = BASE_DIR / 'uploads'` — жёстко заданный путь
- `_get_base_dir()` для frozen: `Path(sys._MEIPASS).parent` — уводил в `%TEMP%`

**Стало:**
- `_get_base_dir()` для frozen: `Path(sys._MEIPASS)` — ресурсы (модель, templates) берутся из временной папки PyInstaller
- Добавлена `_get_default_upload_folder()`:
  - В frozen-режиме: `%LOCALAPPDATA%/ECG_Analysis/uploads` (постоянное место)
  - В dev-режиме: `BASE_DIR / 'uploads'`
- `resolve_upload_folder()`:
  1. Переопределение через GUI (хранится в `.session_state.json`)
  2. Переменная окружения `ECG_UPLOAD_FOLDER`
  3. `_get_default_upload_folder()` (постоянное место для пользовательских данных)
- `UPLOAD_FOLDER` в Config class: `_get_default_upload_folder()` вместо `BASE_DIR / 'uploads'`

### 2. `app/__init__.py`

**Было:**
- `upload_folder.mkdir()` и `SessionStore()` через `app.config['UPLOAD_FOLDER']`
- Health-check: `from ..services.densenet_multiclass_classifier` (две точки — неверный относительный импорт)
- После перезапуска приложения выбранная папка сбрасывалась на default

**Стало:**
- Вызывается `resolve_upload_folder(app.config)` при старте, результат в `app.config['UPLOAD_FOLDER_RESOLVED']`
- После `init_session_storage()` — проверка сохранённого `upload_folder_override` из `.session_state.json`:
  - Если override есть — `UPLOAD_FOLDER_RESOLVED` перезаписывается выбранной пользователем папкой
  - Папка создаётся через `mkdir(parents=True, exist_ok=True)`
- Health-check: `from .services.densenet_multiclass_classifier` (одна точка)

### 3. `app/routes/upload.py`

**Было:** Все обращения к папке через `current_app.config['UPLOAD_FOLDER']`.

**Стало:** Все обращения через `current_app.config.get('UPLOAD_FOLDER_RESOLVED', current_app.config['UPLOAD_FOLDER'])`.

Изменены: `init_session_storage()`, `get_session_store()`, `_metadata_path()`, `upload_file()`.

### 4. `app/services/session_store.py`

**Было:** Только `get/set_threshold()`.

**Стало:** Добавлены методы:
- `get_upload_folder_override()` — прочитать путь из настроек
- `set_upload_folder_override(folder)` — сохранить/сбросить путь
- `clear_all()` — очистить все записи, сохранив настройки

### 5. `app/routes/session.py`

**Было:** Нет endpoint'ов для управления папкой.

**Стало:** Добавлены 5 endpoint'ов:

| Метод | Путь | Описание |
|---|---|---|
| `GET` | `/api/v1/session/folder` | Получить текущую папку, размер, кол-во файлов |
| `POST` | `/api/v1/session/folder` | Установить новую папку (или сбросить к умолчанию) |
| `GET` | `/api/v1/session/folder/stats` | Обновить статистику папки |
| `POST` | `/api/v1/session/folder/clear` | Очистить папку (удалить все файлы) |
| `POST` | `/api/v1/session/pick-folder` | Открыть нативный диалог выбора папки (Tkinter) |

### 6. `app/services/watcher.py`

**Было:** XML-файлы копировались всегда в `app.config['UPLOAD_FOLDER']` (жёстко `uploads/`).

**Стало:** XML-файлы копируются через `app.config.get('UPLOAD_FOLDER_RESOLVED', app.config['UPLOAD_FOLDER'])`, то есть в ту же папку, что выбрана пользователем. JSON-конфиги (`.watcher_config.json`, `.watched_state.json`, `.watcher_audit.jsonl`) остались в корне проекта.

### 7. `app/static/js/icons.js`

**Было:** Иконки: `heart`, `upload`, `download`, `file`, `check`, `checkCircle`, `alert`, `alertCircle`, `info`, `x`, `chart`, `activity`, `eye`, `eyeOff`, `sort`, `filter`, `trash`, `play`, `settings`, `arrowLeft`, `layers`, `zap`, `heartPulse`, `filePlus`, `in`, `fileText`, `archive`.

**Стало:** Добавлена иконка `folder`.

### 8. `app/static/js/upload.js`

**Было:** Нет функций управления папкой.

**Стало:** Добавлены функции:
- `initFolderCard()` — привязка обработчиков кнопок и модалки
- `loadFolderConfig()` — загрузка данных о папке с сервера
- `updateFolderCardUI(data)` — обновление отображения в карточке
- `openFolderConfigModal()` / `closeFolderConfigModal()` — открыть/закрыть модалку
- `pickFolder()` — выбор папки (серверный Tkinter-диалог)
- `saveFolderConfig()` — сохранить новую папку
- `resetFolderConfig()` — сбросить к папке по умолчанию
- `clearFolder()` — очистить папку с подтверждением
- `refreshFolderStats()` — обновить статистику
- Автоматический вызов `refreshFolderStats()` после:
  - ручной загрузки файлов (`uploadFiles()`)
  - удаления одного/нескольких/всех файлов (`deleteFile()`, `deleteSelected()`, `deleteAll()`)
  - импорта новых файлов watcher'ом (`refreshWatcherStatus()` — при появлении новых `imported_count`)

### 9. `app/templates/index.html`

**Было:** Только карточка watcher'а.

**Стало:** Добавлена карточка "Папка временных файлов" с отображением пути, размера и количества файлов, кнопками «Настроить» и «Очистить». Добавлено модальное окно настройки с полем ввода, кнопкой «Обзор…» и кнопкой «Сбросить к умолчанию».

### 10. `app/static/css/main.css`

**Было:** Стили только для `.watcher-card`.

**Стало:** Добавлены стили `.folder-card` (аналогично watcher-карточке).

### 11. `desktop/launcher.py`

**Было:** `sys.path.insert(0, str(Path(__file__).parent.parent))` — не работало в frozen exe.

**Стало:** Добавлена проверка `sys.frozen`:
```python
if getattr(sys, 'frozen', False):
    sys.path.insert(0, str(Path(sys._MEIPASS)))
else:
    sys.path.insert(0, str(Path(__file__).parent.parent))
```

---

## API Примеры

```
GET /api/v1/session/folder
→ { "success": true, "folder": "C:/path/to/folder", "size_formatted": "10.5 MB", "file_count": 42, "xml_count": 40 }

POST /api/v1/session/folder
→ { "folder": "D:/ecg_temp" }  — установить новую папку
→ { "folder": null }            — сбросить к умолчанию

POST /api/v1/session/folder/clear
→ { "success": true, "message": "Папка очищена. Удалено файлов: 42", "deleted": 42 }

POST /api/v1/session/pick-folder
→ { "success": true, "folder": "D:/ecg_temp" }  — Tkinter-диалог
```

## PyInstaller

### Пути по умолчанию

| Режим | Ресурсы (модель, templates, static) | Пользовательские данные (uploads) |
|---|---|---|
| Dev (`python run.py`) | `<проект>/models`, `<проект>/templates` | `<проект>/uploads` |
| Frozen (exe) | `sys._MEIPASS/models`, `sys._MEIPASS/templates` | `%LOCALAPPDATA%/ECG_Analysis/uploads` |

- `_get_base_dir()` → `sys._MEIPASS` — для доступа к встроенным ресурсам
- `_get_default_upload_folder()` → `%LOCALAPPDATA%/ECG_Analysis/uploads` — постоянное хранилище, не сбрасывается при перезапуске
- Выбранная пользователем папка через GUI сохраняется в `.session_state.json` и восстанавливается после перезапуска
- Переменная окружения `ECG_UPLOAD_FOLDER` — принудительное переопределение

### Сборка

Требуется CPU-only версия PyTorch (без CUDA) для минимизации размера exe.
