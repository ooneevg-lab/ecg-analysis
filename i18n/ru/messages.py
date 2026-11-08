"""
Russian error messages and UI text.
"""

# Error messages
ERRORS = {
    'file_not_found': 'Файл не найден: {path}',
    'invalid_extension': 'Неверное расширение файла. Ожидается: {expected}',
    'xml_parse_error': 'Ошибка разбора XML: {details}',
    'missing_leads': 'Отсутствуют отведения: {leads}',
    'short_recording': 'Слишком короткая запись: {duration:.1f} сек (минимум {min_duration} сек)',
    'invalid_data': 'Некорректные данные: {details}',
    'model_not_loaded': 'Модель не загружена',
    'model_not_found': 'Файл модели не найден: {path}',
    'stats_not_found': 'Файл статистик не найден: {path}',
    'classification_error': 'Ошибка классификации: {details}',
    'rhythm_analysis_error': 'Ошибка анализа ритма: {details}',
    'insufficient_peaks': 'Недостаточно R-пиков для анализа ритма (найдено: {count})',
    'preprocessing_error': 'Ошибка предобработки: {details}',
    'export_error': 'Ошибка экспорта: {details}',
    'file_upload_error': 'Ошибка загрузки файла: {details}',
    'session_expired': 'Сессия истекла. Загрузите файл заново.',
    'file_not_in_session': 'Файл не найден в текущей сессии',
}

# Success messages
SUCCESS = {
    'file_uploaded': 'Файл успешно загружен',
    'analysis_complete': 'Анализ завершен',
    'export_complete': 'Отчет сформирован',
}

# UI labels
LABELS = {
    'upload_title': 'Загрузка ЭКГ',
    'upload_hint': 'Перетащите XML-файл сюда или нажмите для выбора',
    'analysis_title': 'Результаты анализа',
    'verdict': 'Заключение',
    'probability': 'Вероятность патологии',
    'rhythm': 'Ритм',
    'heart_rate': 'ЧСС',
    'threshold': 'Порог классификации',
    'export_pdf': 'Экспорт в PDF',
    'lead_names': {
        'I': 'Отведение I',
        'II': 'Отведение II',
        'III': 'Отведение III',
        'aVR': 'Отведение aVR',
        'aVL': 'Отведение aVL',
        'aVF': 'Отведение aVF',
        'V1': 'Отведение V1',
        'V2': 'Отведение V2',
        'V3': 'Отведение V3',
        'V4': 'Отведение V4',
        'V5': 'Отведение V5',
        'V6': 'Отведение V6',
    },
    'processing_steps': {
        'validation': 'Валидация сигнала',
        'preprocessing': 'Предобработка',
        'ai_classification': 'ИИ классификация',
        'xai_analysis': 'Анализ объяснимости',
        'report_generation': 'Формирование отчета',
    }
}

# Verdict translations
VERDICTS = {
    'normal': 'Норма',
    'pathology': 'Патология',
}

# Rhythm class translations
RHYTHM_CLASSES = {
    'normal': 'Нормальный синусовый ритм',
    'bradycardia': 'Брадикардия',
    'tachycardia': 'Тахикардия',
    'arrhythmia': 'Аритмия',
}

# Quality indicators
QUALITY = {
    'excellent': 'Отличное',
    'good': 'Хорошее',
    'acceptable': 'Приемлемое',
    'poor': 'Низкое',
    'sqi_label': 'Индекс качества сигнала',
}


def get_error(key: str, **kwargs) -> str:
    """Get formatted error message."""
    template = ERRORS.get(key, key)
    return template.format(**kwargs) if kwargs else template


def get_success(key: str, **kwargs) -> str:
    """Get formatted success message."""
    template = SUCCESS.get(key, key)
    return template.format(**kwargs) if kwargs else template


def get_label(key: str) -> str:
    """Get UI label."""
    return LABELS.get(key, key)


def get_verdict(key: str) -> str:
    """Get verdict translation."""
    return VERDICTS.get(key, key)


def get_rhythm_class(key: str) -> str:
    """Get rhythm class translation."""
    return RHYTHM_CLASSES.get(key, key)
