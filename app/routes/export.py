"""
Export endpoints for PDF reports.
"""
import io
import threading
from flask import Blueprint, jsonify, request, send_file

from ..routes.upload import get_file_storage, get_storage_lock

export_bp = Blueprint('export', __name__)


@export_bp.route('/pick-save-path', methods=['POST'])
def pick_save_path():
    """
    Open a native Save As dialog via Tk and return the selected path.

    POST /api/v1/export/pick-save-path
    Body: { "filename": "suggested_name.pdf" }

    Returns:
        { "path": "C:/.../file.pdf" } or { "cancelled": true }
    """
    params = request.get_json(silent=True) or {}
    suggested = params.get('filename', 'report.pdf')

    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception as e:
        return jsonify({
            'success': False,
            'error': 'Нативный диалог недоступен (tkinter не установлен).',
        }), 501

    result = {'path': None, 'cancelled': False, 'error': None}

    def _run():
        try:
            root = tk.Tk()
            try:
                root.withdraw()
                try:
                    root.attributes('-topmost', True)
                except Exception:
                    pass
                path = filedialog.asksaveasfilename(
                    title='Сохранить PDF-отчёт',
                    defaultextension='.pdf',
                    filetypes=[('PDF files', '*.pdf'), ('All files', '*.*')],
                    initialfile=suggested,
                )
                result['path'] = path or None
                result['cancelled'] = path is None or path == ''
            finally:
                try:
                    root.destroy()
                except Exception:
                    pass
        except Exception as e:
            result['error'] = f'Диалог завершился с ошибкой: {e}'

    th = threading.Thread(target=_run, daemon=True)
    th.start()
    th.join(timeout=120)
    if th.is_alive():
        return jsonify({
            'success': False,
            'error': 'Тайм-аут ожидания диалога.',
        }), 504

    if result['error']:
        return jsonify({'success': False, 'error': result['error']}), 500

    if result['cancelled'] or not result['path']:
        return jsonify({'success': True, 'cancelled': True})

    return jsonify({'success': True, 'path': result['path']})


@export_bp.route('/pdf/<file_id>', methods=['POST'])
def export_pdf(file_id: str):
    """
    Generate and download PDF report.

    POST /api/export/pdf/<file_id>
    Body (optional): { "save_path": "C:/.../report.pdf" }

    Returns:
        PDF file download
    """
    storage = get_file_storage()

    if file_id not in storage:
        return jsonify({
            'success': False,
            'error': 'Файл не найден'
        }), 404

    file_data = storage[file_id]

    if not file_data['analyzed']:
        return jsonify({
            'success': False,
            'error': 'Анализ еще не выполнен'
        }), 400

    try:
        from ..services.report_generator import PDFReportGenerator, ReportGenerationError

        params = request.get_json(silent=True) or {}
        save_path = params.get('save_path')

        generator = PDFReportGenerator(dpi=150)
        pdf_bytes = generator.generate(file_data['analysis_result'], save_path=save_path)

        # Create filename from original
        original_name = file_data['filename']
        pdf_name = original_name.rsplit('.', 1)[0] + '_report.pdf'

        return send_file(
            io.BytesIO(pdf_bytes),
            mimetype='application/pdf',
            as_attachment=True,
            download_name=pdf_name
        )

    except ImportError as e:
        return jsonify({
            'success': False,
            'error': f'Не установлены необходимые библиотеки: {str(e)}'
        }), 500

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'error': f'Ошибка генерации отчета: {str(e)}'
        }), 500


# ИСПРАВЛЕНО: добавлен эндпоинт для генерации PDF по всем проанализированным файлам
# Раньше: PDF генерировался только для одного файла, массовой экспорта не было
@export_bp.route('/pdf/all', methods=['POST'])
def export_pdf_all():
    """
    Generate and download PDF report for all analyzed files in session.

    POST /api/export/pdf/all
    Body (optional): { "save_path": "C:/.../ecg_report_all.pdf" }

    Returns:
        PDF file download containing all analyzed records
    """
    storage = get_file_storage()

    # Собираем все проанализированные файлы
    with get_storage_lock():
        snapshot = list(storage.items())
    analyzed_results = []
    for file_id, file_data in snapshot:
        if file_data.get('analyzed') and 'analysis_result' in file_data:
            analyzed_results.append({
                'file_id': file_id,
                'filename': file_data.get('filename', 'Неизвестно'),
                'result': file_data['analysis_result']
            })

    if not analyzed_results:
        return jsonify({
            'success': False,
            'error': 'Нет проанализированных файлов для экспорта'
        }), 400

    try:
        from ..services.report_generator import PDFReportGenerator, ReportGenerationError

        params = request.get_json(silent=True) or {}
        save_path = params.get('save_path')

        generator = PDFReportGenerator(dpi=150)
        pdf_bytes = generator.generate_multi(analyzed_results, save_path=save_path)

        return send_file(
            io.BytesIO(pdf_bytes),
            mimetype='application/pdf',
            as_attachment=True,
            download_name='ecg_report_all.pdf'
        )

    except ImportError as e:
        return jsonify({
            'success': False,
            'error': f'Не установлены необходимые библиотеки: {str(e)}'
        }), 500

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'error': f'Ошибка генерации отчета: {str(e)}'
        }), 500


# ИСПРАВЛЕНО: экспорт статистики в PDF вместо CSV
# Раньше: CSV файл
# Теперь: PDF с таблицей результатов, отсортированной по убыванию вероятности патологии
@export_bp.route('/stats', methods=['POST'])
def export_stats():
    """
    Export statistics for all analyzed files as PDF.

    POST /api/export/stats
    Body (optional): { "save_path": "C:/.../ecg_stats_report.pdf" }

    Returns:
        PDF file with table: №, Файл, Вероятность патологии (%), Вердикт
        Sorted by probability descending.
    """
    storage = get_file_storage()

    # Собираем все проанализированные файлы
    with get_storage_lock():
        snapshot = list(storage.items())
    rows = []
    for file_id, file_data in snapshot:
        if file_data.get('analyzed') and 'analysis_result' in file_data:
            result = file_data['analysis_result']
            prob = result.get('probability', 0)
            prediction = result.get('prediction', 0)
            rhythm_class = result.get('rhythm_class', 'normal')

            pred_class = result.get('pred_class', '')
            if prediction == 1:
                verdict = 'Патология'
            elif pred_class == 'SR':
                verdict = 'Синусовый ритм'
            elif rhythm_class and rhythm_class != 'normal':
                verdict = result.get('rhythm_class_ru', 'Нарушение ритма')
            else:
                verdict = 'Норма'

            rows.append({
                'filename': file_data.get('filename', 'Неизвестно'),
                'probability': prob,
                'verdict': verdict
            })

    if not rows:
        return jsonify({
            'success': False,
            'error': 'Нет проанализированных файлов для экспорта'
        }), 400

    # Сортировка по убыванию вероятности
    rows.sort(key=lambda x: x['probability'], reverse=True)

    try:
        from ..services.report_generator import PDFReportGenerator

        params = request.get_json(silent=True) or {}
        save_path = params.get('save_path')

        generator = PDFReportGenerator(dpi=150)
        pdf_bytes = generator.generate_stats_pdf(rows, save_path=save_path)

        return send_file(
            io.BytesIO(pdf_bytes),
            mimetype='application/pdf',
            as_attachment=True,
            download_name='ecg_stats_report.pdf'
        )

    except ImportError as e:
        return jsonify({
            'success': False,
            'error': f'Не установлены необходимые библиотеки: {str(e)}'
        }), 500

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'error': f'Ошибка генерации отчёта: {str(e)}'
        }), 500
