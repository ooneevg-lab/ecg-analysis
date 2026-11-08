"""
Text templates for PDF report generation.
"""

REPORT_TITLE = "Результаты анализа ЭКГ"

REPORT_SECTIONS = {
    'patient_info': "Информация о записи",
    'ai_verdict': "Заключение ИИ",
    'rhythm_analysis': "Анализ ритма",
    'hrv_metrics': "Показатели вариабельности ритма",
    'signal_quality': "Качество сигнала",
    'ecg_traces': "ЭКГ-кривые",
    'xai_analysis': "Анализ объяснимости модели",
    'disclaimer': "Отказ от ответственности",
}

HRV_LABELS = {
    'hr_bpm': ('ЧСС', 'уд/мин'),
    'mean_rr': ('Средний RR', 'мс'),
    'sdnn': ('SDNN', 'мс'),
    'rmssd': ('RMSSD', 'мс'),
    'pnn50': ('pNN50', '%'),
    'rr_cv': ('CV RR', ''),
    'n_peaks': ('Число R-пиков', ''),
}

QUALITY_LEVELS = {
    (0.8, 1.0): ('Отличное', '#28a745'),
    (0.6, 0.8): ('Хорошее', '#5cb85c'),
    (0.4, 0.6): ('Приемлемое', '#ffc107'),
    (0.0, 0.4): ('Низкое', '#dc3545'),
}


def get_quality_level(sqi: float) -> tuple[str, str]:
    """Get quality level name and color for SQI value."""
    for (low, high), (name, color) in QUALITY_LEVELS.items():
        if low <= sqi < high:
            return name, color
    return 'Неизвестно', '#6c757d'


def format_hrv_value(key: str, value: float) -> str:
    """Format HRV metric value with units."""
    if key not in HRV_LABELS:
        return f"{value:.2f}"

    label, unit = HRV_LABELS[key]
    if key == 'pnn50':
        return f"{value * 100:.1f} {unit}"
    elif key in ('hr_bpm', 'n_peaks'):
        return f"{value:.0f} {unit}"
    elif key == 'rr_cv':
        return f"{value:.3f}"
    else:
        return f"{value:.1f} {unit}"
