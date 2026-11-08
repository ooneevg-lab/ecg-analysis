"""
Flask application factory.
"""
import os
import sys
import time
from pathlib import Path
from flask import Flask
from flask_cors import CORS

from .config import config, resolve_upload_folder


def create_app(config_name: str = None) -> Flask:
    """Create and configure the Flask application.

    Args:
        config_name: Configuration name ('development', 'production', or 'default')

    Returns:
        Configured Flask application instance
    """
    if config_name is None:
        config_name = os.environ.get('FLASK_ENV', 'default')

    app = Flask(__name__)

    # When running as a PyInstaller bundle, Flask's default instance_path points
    # inside sys._MEIPASS (a temp dir that is cleaned up on exit).  Override it
    # to a stable directory next to the executable so that persisted JSON files
    # (session state, watcher config, watcher state) survive restarts.
    if getattr(sys, 'frozen', False):
        app.instance_path = Path(sys.executable).parent / 'instance'

    app.config.from_object(config[config_name])

    # Enable CORS for local development
    CORS(app, resources={r"/api/v1/*": {"origins": "*"}})

    # Resolve and ensure upload folder exists
    upload_folder = resolve_upload_folder(app.config)
    app.config['UPLOAD_FOLDER_RESOLVED'] = str(upload_folder)
    upload_folder.mkdir(parents=True, exist_ok=True)

    from .routes.upload import init_session_storage
    app.extensions['session_store'] = init_session_storage(app)

    # Apply persisted folder override from previous session (if any)
    session_store = app.extensions['session_store']
    override = session_store.get_upload_folder_override()
    if override:
        app.config['UPLOAD_FOLDER_RESOLVED'] = override
        Path(override).mkdir(parents=True, exist_ok=True)

    # Register blueprints (v1 API)
    from .routes.upload import upload_bp
    from .routes.analysis import analysis_bp
    from .routes.session import session_bp
    from .routes.export import export_bp
    from .routes.watcher import watcher_bp

    API_V1_PREFIX = '/api/v1'
    app.register_blueprint(upload_bp, url_prefix=f'{API_V1_PREFIX}/upload')
    app.register_blueprint(analysis_bp, url_prefix=f'{API_V1_PREFIX}/analysis')
    app.register_blueprint(session_bp, url_prefix=f'{API_V1_PREFIX}/session')
    app.register_blueprint(export_bp, url_prefix=f'{API_V1_PREFIX}/export')
    app.register_blueprint(watcher_bp, url_prefix=f'{API_V1_PREFIX}/watcher')

    # Optional: folder watcher for the cardiograph export pipeline.
    # Only auto-starts when WATCHED_FOLDER is set (env var ECG_WATCH_FOLDER).
    from .services.watcher import init_watcher
    app.extensions['watcher'] = init_watcher(app)

    # Pre-load DenseNet model at startup to avoid first-request latency
    from .routes.analysis import get_classifier
    with app.app_context():
        try:
            get_classifier()
            print("DenseNet model pre-loaded")
        except Exception as e:
            print(f"Model pre-load failed: {e}")

    # Health check endpoint
    @app.route('/api/health', methods=['GET'])
    def health_check():
        from flask import jsonify
        import time

        watcher = app.extensions.get('watcher')
        model_loaded = False
        try:
            from .services.densenet_multiclass_classifier import DenseNetMulticlassECGClassifier
            model_path = app.config.get('MODEL_PATH')
            if model_path and Path(model_path).exists():
                model_loaded = True
        except Exception:
            pass

        return jsonify({
            'status': 'ok',
            'model_loaded': model_loaded,
            'watcher_running': watcher._observer is not None if watcher else False,
            'uptime_sec': int(time.time() - app.start_time) if hasattr(app, 'start_time') else 0,
            'version': '1.0.0',
        })

    # Register main page route
    @app.route('/')
    def index():
        from flask import render_template
        return render_template('index.html')

    @app.route('/analysis/<file_id>')
    def analysis_page(file_id):
        from flask import render_template
        from .routes.upload import mark_file_viewed
        mark_file_viewed(file_id)
        return render_template('analysis.html', file_id=file_id)

    # Record start time for uptime
    app.start_time = time.time()

    return app
