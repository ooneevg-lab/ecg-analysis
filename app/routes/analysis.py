"""
ECG analysis endpoints.
"""
import traceback

import numpy as np
from flask import Blueprint, current_app, jsonify, request

from ..routes.upload import current_threshold, get_file_storage, get_storage_lock, persist_file_record, set_current_threshold
from ..services.densenet_multiclass_classifier import DenseNetMulticlassECGClassifier
from ..services.explainer import ECGExplainer
from ..services.preprocessor import ECGPreprocessor
from ..services.rhythm_classifier import RhythmClassifier

analysis_bp = Blueprint("analysis", __name__)

_classifier: DenseNetMulticlassECGClassifier | None = None
_rhythm_classifier = None


def _default_threshold() -> float:
    try:
        return current_threshold()
    except RuntimeError:
        pass
    rules = current_app.config.get("CLASSIFICATION_THRESHOLD_RULES") or []
    if rules and isinstance(rules[0], dict) and "threshold" in rules[0]:
        try:
            return float(rules[0]["threshold"])
        except (TypeError, ValueError):
            pass
    return float(current_app.config.get("DEFAULT_THRESHOLD", 0.35))


def get_classifier() -> DenseNetMulticlassECGClassifier:
    """Get or create the single shared classifier instance.

    Only one model is loaded in memory regardless of threshold values.
    Threshold rules are passed at predict() time.
    """
    global _classifier
    if _classifier is None:
        model_path = current_app.config["MODEL_PATH"]
        _classifier = DenseNetMulticlassECGClassifier(str(model_path))
        _classifier.load()
    return _classifier


def get_rhythm_classifier():
    """Get or create heart-rate-only rhythm classifier."""
    global _rhythm_classifier
    if _rhythm_classifier is None:
        window_samples = int(current_app.config.get("RHYTHM_INPUT_SAMPLES", 10000))
        _rhythm_classifier = RhythmClassifier(window_samples=window_samples)
    return _rhythm_classifier


def run_analysis_for_file(
    file_id: str,
    *,
    include_xai: bool = True,
    client_threshold=None,
    force_reanalyze: bool = False,
) -> tuple[bool, dict | None, str | None, int]:
    """Run the full analysis pipeline for a single storage entry.

    Returns (success, result_dict_or_None, error_message_or_None, http_status).
    Used by both the HTTP endpoint and the folder watcher (auto-analyze).
    """
    storage = get_file_storage()
    if file_id not in storage:
        return False, None, "Файл не найден в сессии", 404

    file_data = storage[file_id]
    
    if force_reanalyze:
        file_data["analyzed"] = False
        file_data["analysis_result"] = None
        persist_file_record(file_id)
    
    parsed = file_data.get("parsed")
    if parsed is None:
        source_path = file_data.get("source_path")
        if source_path:
            try:
                from pathlib import Path
                from ..services.xml_parser import parse_polyspectrum_xml, validate_parsed_data

                path = Path(source_path)
                if not path.exists() or not path.is_file():
                    return False, None, "Исходный XML-файл не найден для повторного анализа", 404
                parsed = parse_polyspectrum_xml(str(path))
                is_valid, errors = validate_parsed_data(
                    parsed,
                    min_duration_sec=current_app.config.get("MIN_DURATION_SEC", 10.0),
                )
                file_data["parsed"] = parsed
                file_data["is_valid"] = is_valid
                file_data["validation_errors"] = errors
                file_data["parse_error"] = None
                persist_file_record(file_id)
                if not is_valid:
                    return False, None, "; ".join(errors) or "Файл не прошёл валидацию", 400
            except Exception as e:
                return False, None, f"Не удалось повторно разобрать XML: {e}", 400
        else:
            return False, None, "Файл ещё не разобран или был импортирован ранее", 400

    try:
        target_fs = int(current_app.config.get("TARGET_FS", 500))
        model_samples = int(current_app.config.get("MODEL_INPUT_SAMPLES", 5000))

        preprocessor = ECGPreprocessor(target_fs=target_fs, target_samples=model_samples)
        preprocess_result = preprocessor.preprocess(
            parsed["leads"],
            source_fs=int(parsed.get("sampling_rate", 2000)),
            apply_filter=False,
            apply_notch=False,
        )

        if preprocess_result.get("skipped"):
            return False, None, preprocess_result.get(
                "skip_reason", "Р¤Р°Р№Р» СЃР»РёС€РєРѕРј РєРѕСЂРѕС‚РєРёР№ РґР»СЏ Р°РЅР°Р»РёР·Р°"
            ), 400

        leads_resampled_full = preprocess_result["leads_full"]
        leads_for_model = (leads_resampled_full[:, :model_samples] * 1000.0).astype(np.float32)
        leads_for_display = preprocess_result["leads"]

        if client_threshold is None:
            analysis_threshold = current_threshold()
        else:
            analysis_threshold = set_current_threshold(float(client_threshold))

        classifier = get_classifier()
        analysis_threshold_rounded = round(analysis_threshold, 4)
        threshold_rules = [
            {"source_class": "SR", "threshold": analysis_threshold_rounded, "target_class": "PATHOLOG"},
            {"source_class": "NORM", "threshold": analysis_threshold_rounded, "target_class": "PATHOLOG"},
        ]
        classification = classifier.predict(leads_for_model, threshold_rules=threshold_rules)

        rhythm_classifier = get_rhythm_classifier()
        try:
            rhythm_result = rhythm_classifier.analyze(leads_resampled_full, fs=target_fs)
        except Exception as e:
            rhythm_result = {
                "rhythm_class": "unknown",
                "rhythm_class_ru": "РќРµ СѓРґР°Р»РѕСЃСЊ РѕРїСЂРµРґРµР»РёС‚СЊ",
                "hr_bpm": 0,
                "triggered_rules": [],
                "r_peaks": [],
                "error": str(e),
            }

        xai_result = None
        if include_xai:
            try:
                explainer = ECGExplainer(
                    classifier.model,
                    classifier.mean,
                    classifier.std,
                    classifier.device,
                )
                xai_result = explainer.explain(leads_for_model, method="saliency")
                # Keep only lightweight xai data; saliency_map is computed on-demand
                xai_result = {
                    "top_leads": xai_result.get("top_leads", []),
                    "method": xai_result.get("method", "saliency"),
                }
            except Exception as e:
                xai_result = {"error": str(e), "top_leads": [], "method": "saliency"}

        applied_threshold = analysis_threshold_rounded
        # Store only lightweight data; full signals reloaded on-demand from XML
        result = {
            "file_id": file_id,
            "filename": file_data["filename"],
            **classification,
            "pathology_probability": classification.get("probability", 0),
            "class_probability": classification.get("confidence", 0),
            "threshold": applied_threshold,
            "threshold_rules": threshold_rules,
            "source_sampling_rate": int(parsed.get("sampling_rate", 2000)),
            "sampling_rate": target_fs,
            "model_input_sampling_rate": target_fs,
            "model_input_samples": model_samples,
            "model_input_units": "mV",
            "model_input_scale": 1000.0,
            "resampled_samples": int(leads_resampled_full.shape[1]),
            "resampled_duration_sec": round(float(leads_resampled_full.shape[1]) / float(target_fs), 3),
            "rhythm_class": rhythm_result.get("rhythm_class", "unknown"),
            "rhythm_class_ru": rhythm_result.get("rhythm_class_ru", "РќРµРёР·РІРµСЃС‚РЅРѕ"),
            "hr_bpm": rhythm_result.get("hr_bpm", 0),
            "triggered_rules": rhythm_result.get("triggered_rules", []),
            "rhythm_window_samples": rhythm_result.get("rhythm_window_samples", 0),
            "rhythm_window_sec": rhythm_result.get("rhythm_window_sec", 0),
            "overall_sqi": float(preprocess_result["overall_sqi"]),
            "sqi_per_lead": preprocess_result["sqi_per_lead"].tolist(),
            "morphology_pass": preprocess_result["morphology_pass"],
            "bad_channels": preprocess_result["bad_channels"],
            "xai": xai_result,
            # Lightweight preview data (10s @ 500Hz = 5000 samples) for immediate display
            "ecg_data": leads_for_display.tolist(),
            # Metadata for on-demand full signal loading
            "signal_source": "memory",
            "has_full_signal": True,
            "has_saliency": include_xai and "error" not in xai_result,
            "has_r_peaks": bool(rhythm_result.get("r_peaks")),
        }

        with get_storage_lock():
            storage[file_id]["analyzed"] = True
            storage[file_id]["analysis_result"] = result
            source_path = storage[file_id].get("source_path")
        if source_path:
            watcher = current_app.extensions.get("watcher")
            if watcher is not None and hasattr(watcher, "_state"):
                try:
                    watcher._state.set_flag(source_path, analyzed=True, threshold=applied_threshold)
                except Exception:
                    pass
        persist_file_record(file_id)
        return True, result, None, 200

    except Exception as e:
        traceback.print_exc()
        return False, None, f"РћС€РёР±РєР° Р°РЅР°Р»РёР·Р°: {str(e)}", 500


@analysis_bp.route("/run/<file_id>", methods=["POST"])
def run_analysis(file_id: str):
    """Run ECG analysis for one uploaded XML file."""
    params = request.get_json() or {}
    include_xai = bool(params.get("include_xai", True))
    client_threshold = params.get("threshold")
    force_reanalyze = bool(params.get("force_reanalyze", False))
    if client_threshold is not None:
        try:
            client_threshold = set_current_threshold(float(client_threshold))
        except (TypeError, ValueError):
            return jsonify({"success": False, "error": "Некорректный порог"}), 400

    success, result, error, status = run_analysis_for_file(
        file_id,
        include_xai=include_xai,
        client_threshold=client_threshold,
        force_reanalyze=force_reanalyze,
    )
    if success:
        return jsonify({"success": True, "result": result})
    return jsonify({"success": False, "error": error}), status


@analysis_bp.route("/result/<file_id>", methods=["GET"])
def get_result(file_id: str):
    """Get cached analysis result."""
    storage = get_file_storage()

    if file_id not in storage:
        return jsonify({"success": False, "error": "Р¤Р°Р№Р» РЅРµ РЅР°Р№РґРµРЅ"}), 404

    file_data = storage[file_id]
    if not file_data["analyzed"]:
        return jsonify({"success": False, "error": "РђРЅР°Р»РёР· РµС‰Рµ РЅРµ РІС‹РїРѕР»РЅРµРЅ"}), 400

    return jsonify({"success": True, "result": file_data["analysis_result"]})


@analysis_bp.route("/signal/<file_id>", methods=["GET"])
def get_full_signal(file_id: str):
    """
    Load full ECG signal on-demand from source XML.
    
    GET /api/v1/analysis/signal/<file_id>
    
    Returns:
        JSON with full signal data or error if unavailable.
    """
    from ..routes.upload import load_full_signal
    result = load_full_signal(file_id)
    if result is None:
        return jsonify({"success": False, "error": "Файл не найден"}), 404
    if isinstance(result, dict) and "error" in result:
        return jsonify({"success": False, "error": result["error"]}), 404
    return jsonify({"success": True, "signal": result})
