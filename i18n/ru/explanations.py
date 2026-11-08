"""
Russian text templates for AI explanations and reports.
"""

# Template categories
TEMPLATES = {
    # Normal results
    'normal_high_conf': (
        'Анализ показал нормальную сердечную активность '
        '(вероятность патологии: {prob:.1%}). '
        'Наибольший вклад в классификацию внесли отведения: {top_leads}.'
    ),
    'normal_moderate_conf': (
        'Вероятность патологии низкая ({prob:.1%}), однако рекомендуется '
        'обратить внимание на отведения {top_leads}, где модель обнаружила '
        'незначительные отклонения.'
    ),

    # Pathology results
    'pathology_high_conf': (
        'Выявлены признаки патологии с высокой достоверностью '
        '(вероятность: {prob:.1%}). Основные изменения обнаружены '
        'в отведениях: {top_leads}. Рекомендуется консультация кардиолога.'
    ),
    'pathology_moderate_conf': (
        'Обнаружены потенциальные признаки патологии (вероятность: {prob:.1%}). '
        'Модель отметила изменения в отведениях {top_leads}. '
        'Рекомендуется дополнительное обследование.'
    ),
    'pathology_arrhythmia': (
        'Выявлены признаки аритмии. ЧСС: {hr_bpm:.0f} уд/мин, '
        'вариабельность RR: CV={rr_cv:.2f}. '
        'Сработавшие критерии: {triggered_rules}. '
        'Рекомендуется консультация специалиста.'
    ),

    # Rhythm-specific
    'bradycardia': (
        'Выявлена брадикардия: ЧСС {hr_bpm:.0f} уд/мин '
        '(ниже нормы {threshold} уд/мин). '
        'Рекомендуется оценка клинической значимости.'
    ),
    'tachycardia': (
        'Выявлена тахикардия: ЧСС {hr_bpm:.0f} уд/мин '
        '(выше нормы {threshold} уд/мин). '
        'Рекомендуется исключить причины учащенного ритма.'
    ),
    'arrhythmia': (
        'Выявлена аритмия при нормальной средней ЧСС ({hr_bpm:.0f} уд/мин). '
        'Критерии нерегулярности: {triggered_rules}.'
    ),
    'normal_rhythm': (
        'Ритм синусовый, регулярный. ЧСС: {hr_bpm:.0f} уд/мин.'
    ),

    # Quality warnings
    'low_quality_warning': (
        'Внимание: качество сигнала в отведениях {bad_channels} снижено. '
        'Это может влиять на достоверность результатов.'
    ),
    'quality_acceptable': (
        'Качество записи удовлетворительное. Индекс качества: {sqi:.0%}.'
    ),

    # Lead contributions
    'top_leads_explanation': (
        'Наибольший вклад в решение модели внесли:\n'
        '1. {lead1} — {contrib1:.1f}%\n'
        '2. {lead2} — {contrib2:.1f}%\n'
        '3. {lead3} — {contrib3:.1f}%'
    ),

    # Summary
    'summary_normal': (
        'Заключение: ЭКГ в пределах нормы.\n'
        'Ритм: {rhythm}\n'
        'ЧСС: {hr_bpm:.0f} уд/мин\n'
        'Вероятность патологии по ИИ: {prob:.1%}'
    ),
    'summary_pathology': (
        'Заключение: Выявлены отклонения от нормы.\n'
        'Ритм: {rhythm}\n'
        'ЧСС: {hr_bpm:.0f} уд/мин\n'
        'Вероятность патологии по ИИ: {prob:.1%}\n'
        'Ключевые отведения: {top_leads}'
    ),

    # Disclaimer
    'disclaimer': (
        'Данный анализ выполнен автоматически с использованием '
        'искусственного интеллекта и предназначен только для '
        'предварительной оценки. Окончательный диагноз должен '
        'устанавливаться квалифицированным врачом на основании '
        'полного клинического обследования.'
    ),
}


def format_explanation(template_key: str, **kwargs) -> str:
    """
    Format explanation template with provided values.

    Args:
        template_key: Key from TEMPLATES dict
        **kwargs: Values to substitute

    Returns:
        Formatted explanation string
    """
    template = TEMPLATES.get(template_key, '')
    if not template:
        return ''

    try:
        return template.format(**kwargs)
    except KeyError as e:
        return f"[Ошибка формирования текста: отсутствует {e}]"


def generate_full_explanation(analysis_result: dict) -> str:
    """
    Generate complete explanation text for analysis result.

    Args:
        analysis_result: dict with keys:
            - probability: float
            - prediction: int (0=normal, 1=pathology)
            - rhythm_class: str
            - hr_bpm: float
            - rr_cv: float (optional)
            - triggered_rules: list (optional)
            - top_leads: list of dicts (optional)
            - overall_sqi: float (optional)
            - bad_channels: list (optional)

    Returns:
        Complete explanation text in Russian
    """
    lines = []

    prob = analysis_result.get('probability', 0)
    prediction = analysis_result.get('prediction', 0)
    rhythm_class = analysis_result.get('rhythm_class', 'normal')
    hr_bpm = analysis_result.get('hr_bpm', 0)

    # Main verdict
    if prediction == 0:
        if prob < 0.2:
            template_key = 'normal_high_conf'
        else:
            template_key = 'normal_moderate_conf'
    else:
        if rhythm_class == 'arrhythmia':
            template_key = 'pathology_arrhythmia'
        elif prob > 0.7:
            template_key = 'pathology_high_conf'
        else:
            template_key = 'pathology_moderate_conf'

    # Format top leads string
    top_leads = analysis_result.get('top_leads', [])
    if top_leads:
        top_leads_str = ', '.join([l['lead_name'] for l in top_leads[:3]])
    else:
        top_leads_str = 'II, V5, V6'

    # Format triggered rules
    triggered_rules = analysis_result.get('triggered_rules', [])
    triggered_str = ', '.join(triggered_rules) if triggered_rules else ''

    lines.append(format_explanation(
        template_key,
        prob=prob,
        top_leads=top_leads_str,
        hr_bpm=hr_bpm,
        rr_cv=analysis_result.get('rr_cv', 0),
        triggered_rules=triggered_str
    ))

    # Rhythm info
    rhythm_templates = {
        'bradycardia': 'bradycardia',
        'tachycardia': 'tachycardia',
        'arrhythmia': 'arrhythmia',
        'normal': 'normal_rhythm'
    }
    rhythm_template = rhythm_templates.get(rhythm_class, 'normal_rhythm')
    lines.append('')
    lines.append(format_explanation(
        rhythm_template,
        hr_bpm=hr_bpm,
        threshold=60 if rhythm_class == 'bradycardia' else 100,
        triggered_rules=triggered_str
    ))

    # Quality warning
    bad_channels = analysis_result.get('bad_channels', [])
    overall_sqi = analysis_result.get('overall_sqi', 1.0)

    if bad_channels:
        lines.append('')
        lines.append(format_explanation(
            'low_quality_warning',
            bad_channels=', '.join(bad_channels)
        ))
    elif overall_sqi > 0:
        lines.append('')
        lines.append(format_explanation(
            'quality_acceptable',
            sqi=overall_sqi
        ))

    # Top leads breakdown
    if top_leads and len(top_leads) >= 3:
        lines.append('')
        lines.append(format_explanation(
            'top_leads_explanation',
            lead1=top_leads[0]['lead_name'],
            contrib1=top_leads[0]['contribution'],
            lead2=top_leads[1]['lead_name'],
            contrib2=top_leads[1]['contribution'],
            lead3=top_leads[2]['lead_name'],
            contrib3=top_leads[2]['contribution']
        ))

    # Disclaimer
    lines.append('')
    lines.append(TEMPLATES['disclaimer'])

    return '\n'.join(lines)
