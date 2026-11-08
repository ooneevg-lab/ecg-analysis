"""
PDF report generator for ECG analysis results.

Uses ReportLab for PDF generation (works on Windows without GTK dependencies).
"""
import io
from datetime import datetime
from pathlib import Path

import numpy as np

try:
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    MATPLOTLIB_AVAILABLE = True
except ImportError:
    MATPLOTLIB_AVAILABLE = False

try:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm, cm
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Image, Table, TableStyle,
        PageBreak, KeepTogether
    )
    from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_JUSTIFY
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    REPORTLAB_AVAILABLE = True
except ImportError:
    REPORTLAB_AVAILABLE = False

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from i18n.ru.explanations import generate_full_explanation, TEMPLATES
from utils.signal_processing import CHANNEL_NAMES as LEAD_NAMES  # noqa: E402


class ReportGenerationError(Exception):
    """Exception raised when report generation fails."""
    pass


class PDFReportGenerator:
    """PDF report generator for ECG analysis using ReportLab."""

    # ИСПРАВЛЕНО: регистрация кириллического шрифта
    # Раньше: использовались шрифты по умолчанию (Helvetica), которые не поддерживают кириллицу → ■■■■
    # Теперь: регистрируем TrueType шрифт с поддержкой кириллицы (Arial на Windows)
    _font_name = 'ReportFont'
    _font_registered = False

    def __init__(self, dpi: int = 150):
        """
        Initialize report generator.

        Args:
            dpi: Resolution for embedded images
        """
        self.dpi = dpi

        if not MATPLOTLIB_AVAILABLE:
            raise ReportGenerationError("matplotlib не установлен")
        if not REPORTLAB_AVAILABLE:
            raise ReportGenerationError("reportlab не установлен")

        # Регистрируем кириллический шрифт (один раз)
        if not PDFReportGenerator._font_registered:
            PDFReportGenerator._register_cyrillic_font()

        # Configure matplotlib once (idempotent).
        PDFReportGenerator._configure_matplotlib()

        # Setup styles
        self.styles = getSampleStyleSheet()
        self._setup_styles()

    @staticmethod
    def _register_cyrillic_font():
        """Register a Cyrillic-capable TrueType font for use in PDF."""
        import os
        import glob
        # Расширенный список путей к шрифтам с поддержкой кириллицы
        windir = os.environ.get('WINDIR', 'C:\\Windows')
        font_dirs = [
            os.path.join(windir, 'Fonts', 'arial.ttf'),
            os.path.join(windir, 'Fonts', 'ARIAL.TTF'),
            os.path.join(windir, 'Fonts', 'Arial.ttf'),
            os.path.join(windir, 'Fonts', 'times.ttf'),
            os.path.join(windir, 'Fonts', 'TIMES.TTF'),
            os.path.join(windir, 'Fonts', 'dejavusans.ttf'),
            os.path.join(windir, 'Fonts', 'DejaVuSans.ttf'),
            os.path.join(windir, 'Fonts', 'calibri.ttf'),
            os.path.join(windir, 'Fonts', 'CALIBRI.TTF'),
            os.path.join(windir, 'Fonts', 'consola.ttf'),
        ]

        font_path = None
        for fp in font_dirs:
            if os.path.exists(fp):
                font_path = fp
                break

        # Fallback: ищем любой ttf-шрифт с поддержкой кириллицы
        if font_path is None:
            fonts_dir = os.path.join(windir, 'Fonts')
            if os.path.isdir(fonts_dir):
                # Ищем шрифты, которые обычно поддерживают кириллицу
                candidates = glob.glob(os.path.join(fonts_dir, '*.ttf'))
                candidates += glob.glob(os.path.join(fonts_dir, '*.TTF'))
                # Предпочитаем arial и calibri
                for name in ['arial', 'ARIAL', 'calibri', 'CALIBRI', 'times', 'TIMES', 'consola']:
                    for c in candidates:
                        if name.lower() in os.path.basename(c).lower():
                            if os.path.getsize(c) < 5_000_000:
                                font_path = c
                                break
                    if font_path:
                        break
                # Если не нашли — берём первый маленький ttf
                if font_path is None:
                    for c in sorted(candidates):
                        try:
                            if os.path.getsize(c) < 5_000_000:
                                font_path = c
                                break
                        except OSError:
                            pass

        if font_path:
            try:
                pdfmetrics.registerFont(TTFont(PDFReportGenerator._font_name, font_path))
                PDFReportGenerator._font_registered = True
                print(f"Кириллический шрифт зарегистрирован: {font_path}")
            except Exception as e:
                print(f"Ошибка регистрации шрифта {font_path}: {e}")
        else:
            print("ПРЕДУПРЕЖДЕНИЕ: Не найден шрифт с поддержкой кириллицы. Текст может отображаться некорректно.")

    _matplotlib_configured = False

    @staticmethod
    def _configure_matplotlib() -> None:
        """Set up matplotlib rcParams for Cyrillic support, once per process."""
        if PDFReportGenerator._matplotlib_configured:
            return
        if not MATPLOTLIB_AVAILABLE:
            return
        try:
            matplotlib.rcParams['font.sans-serif'] = ['Arial', 'DejaVu Sans', 'Liberation Sans']
            matplotlib.rcParams['axes.unicode_minus'] = False
            PDFReportGenerator._matplotlib_configured = True
        except Exception as e:
            print(f"Не удалось настроить matplotlib для кириллицы: {e}")

    def _setup_styles(self):
        """Setup custom paragraph styles."""
        # ИСПРАВЛЕНО: используем зарегистрированный кириллический шрифт во всех стилях
        # Раньше: стили наследовали Helvetica (без кириллицы) → ■■■■
        # Теперь: явно задаём fontName = зарегистрированный Cyrillic-шрифт
        font = PDFReportGenerator._font_name if PDFReportGenerator._font_registered else 'Helvetica'

        self.styles.add(ParagraphStyle(
            name='Title_Custom',
            parent=self.styles['Title'],
            fontName=font,
            fontSize=18,
            spaceAfter=20
        ))
        self.styles.add(ParagraphStyle(
            name='Heading2_Custom',
            parent=self.styles['Heading2'],
            fontName=font,
            fontSize=14,
            spaceBefore=15,
            spaceAfter=10
        ))
        self.styles.add(ParagraphStyle(
            name='Body_Custom',
            parent=self.styles['Normal'],
            fontName=font,
            fontSize=10,
            leading=14,
            alignment=TA_JUSTIFY
        ))
        self.styles.add(ParagraphStyle(
            name='Verdict_Normal',
            parent=self.styles['Normal'],
            fontName=font,
            fontSize=16,
            textColor=colors.green,
            alignment=TA_CENTER,
            spaceAfter=10
        ))
        self.styles.add(ParagraphStyle(
            name='Verdict_Warning',
            parent=self.styles['Normal'],
            fontName=font,
            fontSize=16,
            textColor=colors.orange,
            alignment=TA_CENTER,
            spaceAfter=10
        ))
        self.styles.add(ParagraphStyle(
            name='Verdict_Pathology',
            parent=self.styles['Normal'],
            fontName=font,
            fontSize=16,
            textColor=colors.red,
            alignment=TA_CENTER,
            spaceAfter=10
        ))
        self.styles.add(ParagraphStyle(
            name='Disclaimer',
            parent=self.styles['Normal'],
            fontName=font,
            fontSize=8,
            textColor=colors.gray,
            leading=10
        ))

    def _generate_ecg_plot(self, ecg_data: np.ndarray, fs: int,
                           r_peaks: list = None) -> io.BytesIO:
        """
        Generate ECG plot as PNG in memory.

        Args:
            ecg_data: ECG data [12, T]
            fs: Sampling rate
            r_peaks: Optional R-peak positions

        Returns:
            BytesIO object containing PNG image
        """
        # Matplotlib rcParams are configured once via _configure_matplotlib();
        # no need to re-import / reconfigure per call.
        if ecg_data.shape[0] != 12:
            ecg_data = ecg_data.T

        n_samples = ecg_data.shape[1]
        time = np.arange(n_samples) / fs

        # Limit to 10 seconds for display
        max_samples = min(n_samples, fs * 10)
        time = time[:max_samples]

        fig, axes = plt.subplots(12, 1, figsize=(10, 14), sharex=True)
        fig.suptitle('12-канальная ЭКГ', fontsize=12, fontweight='bold')

        for i, (ax, name) in enumerate(zip(axes, LEAD_NAMES)):
            data_mv = ecg_data[i, :max_samples] * 1000  # Convert to mV

            ax.plot(time, data_mv, 'b-', linewidth=0.5)
            ax.set_ylabel(name, fontsize=8)
            ax.tick_params(axis='both', labelsize=7)
            ax.grid(True, alpha=0.3)
            ax.set_xlim(0, time[-1])

            # Mark R-peaks
            if r_peaks:
                r_in_range = [p for p in r_peaks if p < max_samples]
                if r_in_range:
                    r_times = np.array(r_in_range) / fs
                    r_values = data_mv[r_in_range]
                    ax.scatter(r_times, r_values, c='red', s=8, zorder=5)

        axes[-1].set_xlabel('Время (с)', fontsize=9)

        plt.tight_layout()

        buf = io.BytesIO()
        fig.savefig(buf, format='png', dpi=self.dpi, bbox_inches='tight')
        plt.close(fig)
        buf.seek(0)

        return buf

    def generate(self, analysis_result: dict, save_path: str = None) -> bytes:
        """
        Generate PDF report.

        Args:
            analysis_result: Full analysis result dict
            save_path: Optional path to save PDF file

        Returns:
            PDF content as bytes
        """
        # Get ECG data
        ecg_data = analysis_result.get('ecg_data')
        if ecg_data is None:
            raise ReportGenerationError("Отсутствуют данные ЭКГ")

        ecg_data = np.array(ecg_data)
        fs = analysis_result.get('sampling_rate', 500)
        r_peaks = analysis_result.get('r_peaks', [])

        # Create PDF buffer
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=A4,
            rightMargin=2*cm,
            leftMargin=2*cm,
            topMargin=2*cm,
            bottomMargin=2*cm
        )

        # Build story (content)
        story = []

        # Title
        now = datetime.now().strftime('%d.%m.%Y %H:%M')
        story.append(Paragraph('Результаты анализа ЭКГ', self.styles['Title_Custom']))
        story.append(Paragraph(f'Дата: {now}', self.styles['Body_Custom']))
        story.append(Paragraph(f'Файл: {analysis_result.get("filename", "—")}', self.styles['Body_Custom']))
        story.append(Spacer(1, 15))

        # Verdict
        story.append(Paragraph('Заключение ИИ', self.styles['Heading2_Custom']))

        verdict = analysis_result.get('verdict', 'Неизвестно')
        prob = analysis_result.get('probability', 0)
        rhythm_class = analysis_result.get('rhythm_class', 'normal')

        # Choose verdict style based on result
        pred_class = analysis_result.get('pred_class', '')
        if analysis_result.get('prediction', 0) == 1:
            verdict_style = 'Verdict_Pathology'
            verdict_text = f'<b>ПАТОЛОГИЯ</b>'
        elif pred_class == 'SR':
            verdict_style = 'Verdict_Warning'
            verdict_text = f'<b>СИНУСОВЫЙ РИТМ</b>'
        elif rhythm_class and rhythm_class != 'normal':
            verdict_style = 'Verdict_Warning'
            verdict_text = f'<b>{analysis_result.get("rhythm_class_ru", "Нарушение ритма").upper()}</b>'
        else:
            verdict_style = 'Verdict_Normal'
            verdict_text = f'<b>НОРМА</b>'

        story.append(Paragraph(verdict_text, self.styles[verdict_style]))
        story.append(Paragraph(
            f'Вероятность патологии: {prob*100:.1f}% (порог: {analysis_result.get("threshold", 0.35)})',
            self.styles['Body_Custom']
        ))
        story.append(Spacer(1, 10))

        # Rhythm info
        story.append(Paragraph('Анализ ритма', self.styles['Heading2_Custom']))

        rhythm_data = [
            ['Параметр', 'Значение'],
            ['Ритм', analysis_result.get('rhythm_class_ru', '—')],
            ['ЧСС', f'{analysis_result.get("hr_bpm", 0):.0f} уд/мин'],
        ]

        rhythm_table = Table(rhythm_data, colWidths=[6*cm, 8*cm])
        font = PDFReportGenerator._font_name if PDFReportGenerator._font_registered else 'Helvetica'
        rhythm_table.setStyle(TableStyle([
            ('FONTNAME', (0, 0), (-1, -1), font),
            ('BACKGROUND', (0, 0), (-1, 0), colors.lightgrey),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.black),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('FONTSIZE', (0, 0), (-1, -1), 10),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
            ('TOPPADDING', (0, 0), (-1, -1), 8),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ]))
        story.append(rhythm_table)
        story.append(Spacer(1, 15))

        # Top leads
        top_leads = (analysis_result.get('xai') or {}).get('top_leads', [])
        if top_leads:
            story.append(Paragraph('Вклад отведений в классификацию', self.styles['Heading2_Custom']))

            leads_data = [['#', 'Отведение', 'Вклад']]
            for i, lead in enumerate(top_leads[:3]):
                leads_data.append([
                    str(i + 1),
                    lead['lead_name'],
                    f'{lead["contribution"]:.1f}%'
                ])

            leads_table = Table(leads_data, colWidths=[1.5*cm, 4*cm, 3*cm])
            leads_table.setStyle(TableStyle([
                ('FONTNAME', (0, 0), (-1, -1), font),
                ('BACKGROUND', (0, 0), (-1, 0), colors.lightgrey),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('FONTSIZE', (0, 0), (-1, -1), 10),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
                ('TOPPADDING', (0, 0), (-1, -1), 6),
                ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
            ]))
            story.append(leads_table)
            story.append(Spacer(1, 15))

        # ECG plot
        story.append(Paragraph('ЭКГ-кривые', self.styles['Heading2_Custom']))

        ecg_plot_buf = self._generate_ecg_plot(ecg_data, fs, r_peaks)
        img = Image(ecg_plot_buf, width=16*cm, height=22*cm)
        story.append(img)
        story.append(Spacer(1, 15))

        # Build PDF
        doc.build(story)

        pdf_bytes = buffer.getvalue()
        buffer.close()

        # Save if path provided
        if save_path:
            with open(save_path, 'wb') as f:
                f.write(pdf_bytes)

        return pdf_bytes

    # ИСПРАВЛЕНО: добавлен метод generate_multi() для генерации PDF по нескольким записям
    # Раньше: генерировался отчёт только для одной записи, массовой экспорта не было
    def generate_multi(self, analyzed_files: list, save_path: str = None) -> bytes:
        """
        Generate PDF report for multiple analyzed files.

        Args:
            analyzed_files: List of dicts with keys: file_id, filename, result (analysis_result)
            save_path: Optional path to save PDF file

        Returns:
            PDF content as bytes
        """
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=A4,
            rightMargin=2*cm,
            leftMargin=2*cm,
            topMargin=2*cm,
            bottomMargin=2*cm
        )

        story = []

        # Title
        now = datetime.now().strftime('%d.%m.%Y %H:%M')
        story.append(Paragraph('Сводный отчёт по анализу ЭКГ', self.styles['Title_Custom']))
        story.append(Paragraph(f'Дата: {now}', self.styles['Body_Custom']))
        story.append(Paragraph(f'Количество записей: {len(analyzed_files)}', self.styles['Body_Custom']))
        story.append(Spacer(1, 15))

        # Summary table
        summary_data = [['#', 'Файл', 'Заключение', 'Вероятность патологии', 'Ритм', 'ЧСС']]

        for i, file_info in enumerate(analyzed_files):
            result = file_info['result']
            prob = result.get('probability', 0)
            rhythm_class = result.get('rhythm_class', 'normal')
            rhythm_ru = result.get('rhythm_class_ru', '—')
            hr = result.get('hr_bpm', 0)

            pred_class = result.get('pred_class', '')
            if result.get('prediction', 0) == 1:
                verdict = 'Патология'
            elif pred_class == 'SR':
                verdict = 'Синусовый ритм'
            elif rhythm_class and rhythm_class != 'normal':
                verdict = rhythm_ru
            else:
                verdict = 'Норма'

            summary_data.append([
                str(i + 1),
                file_info['filename'],
                verdict,
                f'{prob*100:.1f}%',
                rhythm_ru,
                f'{hr:.0f} уд/мин'
            ])

        summary_table = Table(summary_data, colWidths=[1*cm, 4*cm, 3*cm, 3*cm, 3.5*cm, 2.5*cm])
        font = PDFReportGenerator._font_name if PDFReportGenerator._font_registered else 'Helvetica'
        summary_table.setStyle(TableStyle([
            ('FONTNAME', (0, 0), (-1, -1), font),
            ('BACKGROUND', (0, 0), (-1, 0), colors.lightgrey),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.black),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('FONTSIZE', (0, 0), (-1, -1), 8),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
            ('TOPPADDING', (0, 0), (-1, -1), 6),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ]))
        story.append(summary_table)
        story.append(Spacer(1, 20))

        # Detailed sections for each file
        for i, file_info in enumerate(analyzed_files):
            result = file_info['result']

            # Page break between files
            if i > 0:
                story.append(PageBreak())

            story.append(Paragraph(
                f'Запись {i + 1}: {file_info["filename"]}',
                self.styles['Heading2_Custom']
            ))

            # Verdict
            verdict = result.get('verdict', 'Неизвестно')
            prob = result.get('probability', 0)
            rhythm_class = result.get('rhythm_class', 'normal')

            pred_class = result.get('pred_class', '')
            if result.get('prediction', 0) == 1:
                verdict_style = 'Verdict_Pathology'
                verdict_text = '<b>ПАТОЛОГИЯ</b>'
            elif pred_class == 'SR':
                verdict_style = 'Verdict_Warning'
                verdict_text = '<b>СИНУСОВЫЙ РИТМ</b>'
            elif rhythm_class and rhythm_class != 'normal':
                verdict_style = 'Verdict_Warning'
                verdict_text = f'<b>{result.get("rhythm_class_ru", "Нарушение ритма").upper()}</b>'
            else:
                verdict_style = 'Verdict_Normal'
                verdict_text = '<b>НОРМА</b>'

            story.append(Paragraph(verdict_text, self.styles[verdict_style]))
            story.append(Paragraph(
                f'Вероятность патологии: {prob*100:.1f}% (порог: {result.get("threshold", 0.35)})',
                self.styles['Body_Custom']
            ))
            story.append(Spacer(1, 10))

            # Rhythm info
            rhythm_data = [
                ['Параметр', 'Значение'],
                ['Ритм', result.get('rhythm_class_ru', '—')],
                ['ЧСС', f'{result.get("hr_bpm", 0):.0f} уд/мин'],
            ]
            rhythm_table = Table(rhythm_data, colWidths=[6*cm, 8*cm])
            rhythm_table.setStyle(TableStyle([
                ('FONTNAME', (0, 0), (-1, -1), font),
                ('BACKGROUND', (0, 0), (-1, 0), colors.lightgrey),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.black),
                ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                ('FONTSIZE', (0, 0), (-1, -1), 10),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
                ('TOPPADDING', (0, 0), (-1, -1), 8),
                ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
            ]))
            story.append(rhythm_table)
            story.append(Spacer(1, 10))

            # Top leads
            top_leads = result.get('xai', {}).get('top_leads', [])
            if top_leads:
                story.append(Paragraph('Вклад отведений в классификацию', self.styles['Heading2_Custom']))
                leads_data = [['#', 'Отведение', 'Вклад']]
                for j, lead in enumerate(top_leads[:3]):
                    leads_data.append([
                        str(j + 1),
                        lead['lead_name'],
                        f'{lead["contribution"]:.1f}%'
                    ])
                leads_table = Table(leads_data, colWidths=[1.5*cm, 4*cm, 3*cm])
                leads_table.setStyle(TableStyle([
                    ('FONTNAME', (0, 0), (-1, -1), font),
                    ('BACKGROUND', (0, 0), (-1, 0), colors.lightgrey),
                    ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                    ('FONTSIZE', (0, 0), (-1, -1), 10),
                    ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
                    ('TOPPADDING', (0, 0), (-1, -1), 6),
                    ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
                ]))
                story.append(leads_table)
                story.append(Spacer(1, 10))

            # ECG plot
            story.append(Paragraph('ЭКГ-кривые', self.styles['Heading2_Custom']))

            ecg_data = result.get('ecg_data')
            if ecg_data is not None:
                ecg_data = np.array(ecg_data)
                fs = result.get('sampling_rate', 500)
                r_peaks = result.get('r_peaks', [])

                ecg_plot_buf = self._generate_ecg_plot(ecg_data, fs, r_peaks)
                img = Image(ecg_plot_buf, width=16*cm, height=22*cm)
                story.append(img)
                story.append(Spacer(1, 10))

            # Interpretation
            story.append(Paragraph('Интерпретация', self.styles['Heading2_Custom']))
            explanation = generate_full_explanation(result)
            for para in explanation.split('\n\n'):
                if para.strip():
                    story.append(Paragraph(para.strip(), self.styles['Body_Custom']))
                    story.append(Spacer(1, 5))

        story.append(Spacer(1, 20))
        story.append(Paragraph(TEMPLATES['disclaimer'], self.styles['Disclaimer']))

        # Build PDF
        doc.build(story)

        pdf_bytes = buffer.getvalue()
        buffer.close()

        if save_path:
            with open(save_path, 'wb') as f:
                f.write(pdf_bytes)

        return pdf_bytes

    # ИСПРАВЛЕНО: добавлен метод для генерации простого PDF-отчёта статистики
    # Раньше: экспорт был в CSV
    # Теперь: PDF с таблицей всех результатов, отсортированной по убыванию вероятности
    def generate_stats_pdf(self, rows: list, save_path: str = None) -> bytes:
        """
        Generate a simple PDF report with statistics for all analyzed files.

        Args:
            rows: List of dicts with keys: filename, probability, verdict
            save_path: Optional path to save PDF file

        Returns:
            PDF content as bytes
        """
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=A4,
            rightMargin=2*cm,
            leftMargin=2*cm,
            topMargin=2*cm,
            bottomMargin=2*cm
        )

        story = []
        font = PDFReportGenerator._font_name if PDFReportGenerator._font_registered else 'Helvetica'

        # Title
        now = datetime.now().strftime('%d.%m.%Y %H:%M')
        story.append(Paragraph('Статистика анализа ЭКГ', self.styles['Title_Custom']))
        story.append(Paragraph(f'Дата: {now}', self.styles['Body_Custom']))
        story.append(Paragraph(f'Всего записей: {len(rows)}', self.styles['Body_Custom']))
        story.append(Spacer(1, 15))

        # Summary counts
        n_pathology = sum(1 for r in rows if r['verdict'] == 'Патология')
        n_sr = sum(1 for r in rows if r['verdict'] == 'Синусовый ритм')
        n_normal = sum(1 for r in rows if r['verdict'] == 'Норма')
        n_rhythm = len(rows) - n_pathology - n_sr - n_normal
        avg_prob = sum(r['probability'] for r in rows) / len(rows) * 100

        summary_text = (
            f'Патология: {n_pathology} | '
            f'Синусовый ритм: {n_sr} | '
            f'Бради/тахикардия: {n_rhythm} | '
            f'Норма: {n_normal} | '
            f'Средняя вероятность: {avg_prob:.1f}%'
        )
        story.append(Paragraph(summary_text, self.styles['Body_Custom']))
        story.append(Spacer(1, 20))

        # Table
        table_data = [['№', 'Файл', 'Вероятность патологии (%)', 'Вердикт']]
        for i, row in enumerate(rows, 1):
            table_data.append([
                str(i),
                row['filename'],
                f"{row['probability'] * 100:.1f}",
                row['verdict']
            ])

        col_widths = [1*cm, 8*cm, 4*cm, 3.5*cm]
        table = Table(table_data, colWidths=col_widths, repeatRows=1)
        table.setStyle(TableStyle([
            ('FONTNAME', (0, 0), (-1, -1), font),
            ('BACKGROUND', (0, 0), (-1, 0), colors.lightgrey),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.black),
            ('ALIGN', (0, 0), (0, -1), 'CENTER'),
            ('ALIGN', (2, 1), (2, -1), 'CENTER'),
            ('ALIGN', (3, 1), (3, -1), 'CENTER'),
            ('FONTSIZE', (0, 0), (-1, -1), 9),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
            ('TOPPADDING', (0, 0), (-1, -1), 6),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            # Color verdict column
            *[('TEXTCOLOR', (3, i + 1), (3, i + 1),
               colors.red if rows[i]['verdict'] == 'Патология'
               else (colors.orange if 'ритм' in rows[i]['verdict'].lower() else colors.green))
              for i in range(len(rows))],
        ]))
        story.append(table)
        story.append(Spacer(1, 20))

        # Disclaimer
        story.append(Paragraph(TEMPLATES['disclaimer'], self.styles['Disclaimer']))

        # Build PDF
        doc.build(story)

        pdf_bytes = buffer.getvalue()
        buffer.close()

        if save_path:
            with open(save_path, 'wb') as f:
                f.write(pdf_bytes)

        return pdf_bytes
