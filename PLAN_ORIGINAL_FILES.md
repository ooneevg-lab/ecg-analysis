# План: не копировать файлы при ручной загрузке

## Идея

Сейчас приложение **всегда** создаёт копию XML-файла в рабочей папке (`<UUID>.xml`), независимо от того, откуда файл был взят — ручная загрузка через браузер или импорт watcher'ом.

Предлагается: **при ручной загрузке не копировать файл**, а сохранять ссылку на оригинальный путь. Копировать только когда watcher импортирует файл с `delete_after_import = true`.

---

## Мотивация

- Не плодить дубликаты, если оригинал уже лежит в удобном месте
- Экономия дискового пространства
- Прозрачность: пользователь знает, с каким файлом работает

---

## Как это может работать

### 1. Ручная загрузка (через upload zone или drag-and-drop)

**Сейчас:**
```
браузер → FormData → Flask → сохраняет в upload_folder/<UUID>.xml
                                    ↓
                            _file_storage[file_id] = {
                                'permanent_path': 'upload_folder/<UUID>.xml',
                                'original_filename': 'record.xml'
                            }
```

**Предлагается:**
```
браузер → FormData → Flask → сохраняет во временный файл
                              парсит, проверяет валидность
                              если валиден — удаляет временный файл
                                    ↓
                            _file_storage[file_id] = {
                                'original_path': null,       // не применимо (браузер не даёт путь)
                                'permanent_path': null,      // не храним копию
                                'xml_data': '<parsed XML content in memory>',
                                'original_filename': 'record.xml'
                            }
```

**Проблема:** браузер не передаёт исходный путь файла (безопасность). Файл _всегда_ передаётся как stream и должен быть куда-то сохранён на сервере хотя бы временно для парсинга. После парсинга XML можно держать в памяти (как строку) и не писать на диск.

**Риски:**
- Если приложение перезапустится, данные в памяти пропадут — файл надо будет перезагружать
- Для повторного анализа XML нужен снова — либо в памяти, либо с диска

### 2. Импорт watcher'ом

**Сейчас:**
```
watcher находит *.xml → копирует в upload_folder/<UUID>.xml
                        если delete_after_import — удаляет оригинал
```

**Предлагается:**
```
watcher находит *.xml → если delete_after_import = true → копирует в upload_folder/<UUID>.xml
                        если delete_after_import = false → сохраняет original_path = исходный путь
```

Без копии файла при `delete_after_import = false` анализ будет читать XML прямо из исходной папки. Если файл оттуда исчезнет — анализ станет недоступен.

### 3. Хранение XML-данных

Варианты хранения содержимого XML для файлов без копии на диске:

| Вариант | Плюсы | Минусы |
|---------|-------|--------|
| **В памяти** (строка в `_file_storage`) | Не требует диска | Теряется при перезапуске; растёт потребление RAM |
| **Кэш в той же рабочей папке** (как сейчас) | Не теряется при перезапуске; не требует повторного парсинга | Занимает место на диске (дубликат) |
| **Кэш, но с удалением при архивации** | Компромисс | Сложнее логика |

### 4. Модификация `_file_storage`

Сейчас в `_file_storage[file_id]` есть `source_path` (всегда путь к копии на диске).
Предлагается добавить опциональное поле `xml_content` и разрешить `source_path = null`:

```python
_file_storage[file_id] = {
    'filename': 'record.xml',
    'source_path': ...,             # путь к копии на диске (если есть)
    'original_path': ...,           # исходный путь (watcher без delete_after_import)
    'xml_content': ...,             # содержимое XML, если нет файла на диске
    'parsed': ...,                  # распарсенные данные (всегда в памяти)
    'source': 'upload' | 'watcher',
    ...
}
```

**Приоритет при чтении XML для анализа** (функция `_load_signal_data()` в `app/routes/upload.py:175-230`):
1. `parsed` в памяти — если есть, используем сразу
2. `source_path` на диске — если есть, читаем и парсим файл
3. `original_path` на диске — если есть, читаем и парсим оригинал
4. `xml_content` в памяти — если нет ничего другого, парсим из строки

---

## Детальные изменения кода

### A. `app/routes/upload.py` — `upload_file()` (строка 338)

Место где сейчас создаётся копия на диске:

```python
# Сейчас: строки 342-371
upload_folder = Path(current_app.config.get('UPLOAD_FOLDER_RESOLVED', current_app.config['UPLOAD_FOLDER']))
upload_folder.mkdir(parents=True, exist_ok=True)
permanent_path = upload_folder / f"{file_id}.xml"          # ← копия
file.save(str(permanent_path))                              # ← сохраняем на диск
parsed = parse_polyspectrum_xml(str(permanent_path))        # ← парсим с диска
...
source_path = str(permanent_path)
...
_file_storage[file_id] = make_storage_entry(
    file.filename, None,
    parsed=parsed, is_valid=is_valid, errors=errors,
    created_at=created_at,
    source_path=source_path,               # ← сохраняем путь к копии
)
```

**Предлагается заменить на:**

```python
# Новый вариант: сохраняем во временный файл, парсим, удаляем
import tempfile
fd, tmp_path = tempfile.mkstemp(suffix='.xml', prefix='ecg_upload_')
os.close(fd)
tmp_path = Path(tmp_path)
try:
    file.save(str(tmp_path))
    parsed = parse_polyspectrum_xml(str(tmp_path))
    is_valid, errors = validate_parsed_data(
        parsed,
        min_duration_sec=current_app.config.get('MIN_DURATION_SEC', 10.0)
    )
    # Не копируем в upload_folder — храним XML в памяти
    source_path = None
    xml_content = tmp_path.read_text(encoding='utf-8') if is_valid else None
    _file_storage[file_id] = make_storage_entry(
        file.filename, None,
        parsed=parsed, is_valid=is_valid, errors=errors,
        created_at=created_at,
        source_path=None,          # ← нет копии на диске
    )
    _file_storage[file_id]['xml_content'] = xml_content
    persist_file_record(file_id)
finally:
    tmp_path.unlink(missing_ok=True)
```

### B. `app/routes/upload.py` — `_load_signal_data()` (строки 175-230)

Место где сейчас читается XML с диска:

```python
# Сейчас: строки 180-208
source_path = file_data.get('source_path')
if parsed and 'leads' in parsed:
    leads = parsed['leads']
    source_fs = int(parsed.get('sampling_rate', 2000))
elif source_path:
    path = Path(source_path)
    if not path.exists() or not path.is_file():
        return {"error": "Исходный XML-файл не найден. Загрузите файл вручную."}
    parsed = parse_polyspectrum_xml(str(path))
    ...
```

**Предлагается добавить проверки `original_path` и `xml_content`:**

```python
# После проверки parsed, добавить:
elif file_data.get('xml_content'):
    # Парсим из памяти (ручная загрузка без копии)
    from io import StringIO
    parsed = parse_polyspectrum_xml(StringIO(file_data['xml_content']))
    is_valid, errors = validate_parsed_data(parsed, ...)
    ...
elif file_data.get('original_path'):
    # Читаем оригинал (watcher без delete_after_import)
    path = Path(file_data['original_path'])
    if not path.exists():
        return {"error": "Оригинальный XML-файл не найден в папке экспорта."}
    parsed = parse_polyspectrum_xml(str(path))
    ...
```

### C. `app/routes/upload.py` — `make_storage_entry()` (строка 254)

Добавить поле `xml_content`:

```python
# Сейчас: возвращает dict без xml_content
def make_storage_entry(...) -> dict:
    return {
        'filename': filename,
        'parsed': parsed,
        'source_path': source_path,
        'original_path': None,     # ← добавить
        'xml_content': None,       # ← добавить
        ...
    }
```

### D. `app/services/watcher.py` — импорт файла (строки 410-415)

Копировать только при `delete_after_import = True`:

```python
# Сейчас: безусловная копия
if is_valid:
    upload_folder = Path(self.app.config.get('UPLOAD_FOLDER_RESOLVED', ...))
    upload_folder.mkdir(parents=True, exist_ok=True)
    permanent_path = upload_folder / f"{file_id}.xml"
    shutil.copy2(str(path), str(permanent_path))

storage_source_path = str(permanent_path) if permanent_path else key
```

**Предлагается:**

```python
# Новый вариант
if is_valid:
    if self.delete_after:
        # Нужна копия — оригинал будет удалён
        upload_folder = Path(self.app.config.get('UPLOAD_FOLDER_RESOLVED', ...))
        upload_folder.mkdir(parents=True, exist_ok=True)
        permanent_path = upload_folder / f"{file_id}.xml"
        shutil.copy2(str(path), str(permanent_path))
    else:
        # Не копируем — будем читать оригинал
        permanent_path = None

storage_source_path = str(permanent_path) if permanent_path else None
original_path = None if permanent_path else str(path.resolve())

entry = make_storage_entry(
    path.name, None,
    parsed=parsed, is_valid=is_valid, errors=errors,
    created_at=datetime.fromtimestamp(mtime).isoformat(timespec='seconds'),
    source='watcher',
    source_path=storage_source_path,      # None если нет копии
    auto_imported=True,
)
entry['original_path'] = original_path   # ← сохраняем путь к оригиналу
```

### E. `app/services/session_store.py` — `save_record()` / `_restore_record()` (строки 110-135)

Не сериализовать `parsed` (бинарные данные) и `xml_content` (большая строка):

```python
# Сейчас: (строки 118-135)
def save_record(self, file_id: str, record: dict) -> None:
    with self._lock:
        self._data.setdefault("records", {})[file_id] = _to_jsonable({
            k: v for k, v in record.items()
            if k != "parsed"                        # уже не сериализуем parsed
        })
        self._flush_unlocked()

@staticmethod
def _restore_record(file_id: str, data: dict) -> dict:
    record = {
        "filename": data.get("filename"),
        "temp_path": data.get("temp_path"),
        "is_valid": data.get("is_valid", False),
        "validation_errors": data.get("validation_errors", []),
        "parse_error": data.get("parse_error"),
        "analyzed": data.get("analyzed", False),
        "viewed": data.get("viewed", False),
        "archived": data.get("archived", False),
        "analysis_result": data.get("analysis_result"),
        "created_at": data.get("created_at"),
        "source": data.get("source", "upload"),
        "source_path": data.get("source_path"),
        "auto_imported": data.get("auto_imported", False),
        "original_path": data.get("original_path"),   # ← добавить
        # 'xml_content': ...,                         # НЕ сохраняем в JSON
        # 'parsed': None,                              # перепарсивается при загрузке
    }
    return record
```

**Важно:** `xml_content` не сохраняется в `.session_state.json` (иначе JSON будет огромным). После перезапуска приложения файлы, загруженные вручную без копии на диск, будут отображаться в списке, но при попытке открыть анализ будет ошибка «файл не найден, перезагрузите». 

**Альтернатива:** сохранять `xml_content` в отдельный кэш-файл рядом с `.session_state.json`:
- `<file_id>.xml_cache` — сжатый XML (gzip), автоматически удаляется при архивации/удалении файла

### F. `app/routes/upload.py` — `delete_file_record()` / очистка (строки 452-460)

При удалении файла — удалять и копию, и оригинал watcher'а, и xml_content:

```python
# Сейчас:
source_path = file_data.get('source_path')
if source_path:
    try:
        Path(source_path).unlink(missing_ok=True)
    except Exception:
        pass
if source_path and file_data.get('source') == 'watcher':
    try:
        Path(source_path).unlink(missing_ok=True)
    except Exception:
        pass

# Предлагается:
# Удалить копию на диске (если была)
source_path = file_data.get('source_path')
if source_path:
    Path(source_path).unlink(missing_ok=True)
# Удалить кэш xml_content (если был)
xml_cache = Path(file_data.get('_xml_cache_path')) if file_data.get('_xml_cache_path') else None
if xml_cache and xml_cache.exists():
    xml_cache.unlink(missing_ok=True)
# Очистить память
file_data.pop('xml_content', None)
file_data.pop('parsed', None)
```

### G. Резюме — все изменения

| Файл | Функция / место | Строки | Суть изменения |
|------|----------------|--------|----------------|
| `app/routes/upload.py` | `upload_file()` | 342-371 | Не сохранять копию XML; сохранить `xml_content` в памяти |
| `app/routes/upload.py` | `_load_signal_data()` | 175-230 | Читать из `original_path` или `xml_content` если нет `source_path` |
| `app/routes/upload.py` | `make_storage_entry()` | 254-283 | Добавить поля `original_path`, `xml_content` |
| `app/routes/upload.py` | `remove_file_record()` / delete | 440-460 | Не удалять `original_path` (watcher), очищать `xml_content` из памяти |
| `app/services/watcher.py` | `_import_file()` | 410-415 | Копировать только при `delete_after = True` |
| `app/services/watcher.py` | `_import_file()` | 445-451 | Сохранять `original_path` вместо `source_path` если нет копии |
| `app/services/session_store.py` | `save_record()`, `_restore_record()` | 110-135 | Добавить поле `original_path`, исключить `xml_content` из JSON |
| `app/routes/analysis.py` | `run_analysis_for_file()` | ~160 | Передавать `xml_content` вместо пути к файлу (опционально) |

---

## Компромиссы

- **Ручная загрузка через браузер всё равно требует временной записи на диск** (Flask `file.save()`), хотя бы в `tempfile`. От этого никуда не деться — multipart form-data не даёт потокового доступа без сохранения.
- **Watcher без `delete_after_import`** — если исходный файл будет удалён из папки экспорта, анализ перестанет работать. Можно предупредить пользователя в UI.
- **Сохранение XML в памяти** — при тысячах файлов (маловероятно для ЭКГ) это может быть проблемой. Для ЭКГ-файлов типичный XML ~100–500 КБ, 1000 файлов ≈ 100–500 МБ RAM.

---

## Решение принимать не нужно

Этот документ — описание альтернативного подхода для информации, не план к реализации. Если решите перейти на эту схему, её можно доработать и применить.

---

## Упрощённый подход (предложен в обсуждении)

Суть:
- Убрать дублирование XML-файлов
- Не копировать оригиналы, работать с ними напрямую
- Хранить только результаты анализа и пути к оригиналам

### Как это работает

**Ручная загрузка:**
```
браузер → FormData → Flask → сохраняет во временный файл
                              парсит, проверяет валидность
                              если валиден — копирует в выбранную папку (единственная копия)
                                    ↓
                            _file_storage[file_id] = {
                                'filename': 'record.xml',
                                'source_path': 'выбранная_папка/record.xml',
                                'parsed': {...},      # распарсенный сигнал (в памяти)
                            }
```

Браузер не даёт исходный путь — файл всегда передаётся потоком. Но вместо создания UUID-копии можно сохранить его с оригинальным именем прямо в выбранную папку. При повторном открытии XML уже лежит на месте.

**Watcher (без `delete_after_import`):**
```
watcher находит *.xml → парсит, проверяет
                        сохраняет в _file_storage:
                            'original_path': 'папка_экспорта/record.xml'
                            'source_path': null    # копия не создаётся
```

При открытии анализа XML читается прямо из папки экспорта. Если файл оттуда исчез — показывается ошибка «файл не найден».

**Watcher (с `delete_after_import = true`):**
```
watcher находит *.xml → копирует в выбранную папку (как сейчас)
                        удаляет оригинал
                        сохраняет:
                            'source_path': 'выбранная_папка/<UUID>.xml'
```

Без копии не обойтись — оригинал будет удалён.

### Что меняется в UI

- **Убрать кнопки «Удалить файл», «Удалить все», «Удалить выбранные»** — нечего удалять, файлы принадлежат пользователю, а не приложению
- Оставить только **«Архивировать»** (скрыть из списка, не трогая файл на диске)
- Если файл был удалён из папки экспорта (watcher) или из выбранной папки (ручная загрузка) — показывать в списке пометку «файл недоступен»

### Что меняется в коде

| Изменение | Детали |
|-----------|--------|
| **Ручная загрузка** | Вместо `<UUID>.xml` сохранять с оригинальным именем в выбранную папку (или оставить UUID, но это всё равно единственная копия) |
| **Watcher без delete** | Не копировать, сохранять `original_path` |
| **Удаление из UI** | Убрать кнопки удаления во фронтенде (`upload.js`) и соответствующие endpoint'ы |
| **Результаты** | Уже сохраняются в `.session_state.json` — ничего менять не нужно |
| **Пометка «недоступен»** | При загрузке списка проверять `source_path` / `original_path` на существование файла |

### Плюсы

- Нет дублирования — каждый XML хранится в одном экземпляре
- Пользователь всегда знает, где лежит его файл
- Меньше логики (не нужно чистить кэш, удалять копии и т.д.)
- Выбранная папка превращается просто в «папку, куда сохраняются XML при ручной загрузке и куда watcher копирует при delete_after»

### Минусы

- При ручной загрузке через браузер файл всё равно пишется на диск (неизбежно), но уже как единственная копия
- Если watcher без `delete_after` и файл из папки экспорта пропал — анализ недоступен
- При перезапуске приложения `parsed` в памяти теряется, XML читается с диска заново
