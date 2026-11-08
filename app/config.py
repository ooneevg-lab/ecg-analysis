"""
Flask application configuration.
"""
import os
import sys
from pathlib import Path


def _get_base_dir() -> Path:
    """Get base directory for resources (models, templates, static).
    Works with PyInstaller: returns sys._MEIPASS when frozen."""
    if getattr(sys, 'frozen', False):
        return Path(sys._MEIPASS) if hasattr(sys, '_MEIPASS') else Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR = _get_base_dir()


def _get_default_upload_folder() -> Path:
    """Permanent location for user data (XML uploads, cache).
    Uses %LOCALAPPDATA% when frozen to survive PyInstaller temp cleanup."""
    if getattr(sys, 'frozen', False):
        return Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData' / 'Local')) / 'ECG_Analysis' / 'uploads'
    return BASE_DIR / 'uploads'


def resolve_upload_folder(app_config: dict | None = None) -> Path:
    """
    Resolve the upload folder path.
    
    Priority:
    1. User override (stored in session_state.json, set via GUI)
    2. Environment variable ECG_UPLOAD_FOLDER
    3. Default: permanent folder (%LOCALAPPDATA%/ECG_Analysis/uploads when frozen,
       otherwise project-root/uploads)
    """
    if app_config and app_config.get('UPLOAD_FOLDER_OVERRIDE'):
        return Path(app_config['UPLOAD_FOLDER_OVERRIDE'])
    
    env_path = os.environ.get('ECG_UPLOAD_FOLDER')
    if env_path:
        return Path(env_path)
    
    return _get_default_upload_folder()


class Config:
    """Base configuration."""

    SECRET_KEY = os.environ.get('SECRET_KEY', 'ecg-analysis-secret-key-dev')

    # Model paths. The default DenseNet model is bundled inside this GUI project.
    MODEL_PATH = Path(os.environ.get(
        'ECG_MODEL_PATH',
        str(BASE_DIR / 'models' / 'densenet121_multiclass.pt')
    ))
    MODEL_TYPE = os.environ.get('ECG_MODEL_TYPE', 'densenet121_1d_multiclass')

    # Upload settings — default folder for user data
    # When frozen (PyInstaller), this resolves to %LOCALAPPDATA%/ECG_Analysis/uploads
    UPLOAD_FOLDER = _get_default_upload_folder()
    MAX_CONTENT_LENGTH = 50 * 1024 * 1024  # 50 MB max file size
    ALLOWED_EXTENSIONS = {'xml'}

    # ECG processing parameters
    TARGET_FS = 500  # Target sampling rate in Hz
    MODEL_INPUT_SAMPLES = 5000  # First 10 seconds after resampling to 500 Hz
    RHYTHM_INPUT_SAMPLES = 10000  # First 20 seconds after resampling to 500 Hz
    MIN_DURATION_SEC = 10  # Minimum recording duration
    DEFAULT_THRESHOLD = 0.35  # Default threshold for UI and server rules
    CLASSIFICATION_THRESHOLD_RULES = [
        {'source_class': 'SR', 'threshold': 0.35, 'target_class': 'PATHOLOG'},
        {'source_class': 'NORM', 'threshold': 0.35, 'target_class': 'PATHOLOG'},
    ]

    # Watcher for cardiograph export folder. Set ECG_WATCH_FOLDER env var to enable.
    # When unset, the watcher is disabled and the upload zone is the only way in.
    WATCHED_FOLDER = os.environ.get('ECG_WATCH_FOLDER', None)
    WATCHED_STABLE_SEC = float(os.environ.get('ECG_WATCH_STABLE_SEC', 2.0))
    WATCHED_AUTOMATIC_ANALYSIS = os.environ.get('ECG_WATCH_AUTO_ANALYZE', '0') == '1'
    # If True, watcher deletes the original file from the watched folder after import.
    # Default is False: leave the cardiograph's log untouched.
    WATCHED_DELETE_AFTER_IMPORT = os.environ.get('ECG_WATCH_DELETE_AFTER', '0') == '1'


class DevelopmentConfig(Config):
    """Development configuration."""

    DEBUG = True


class ProductionConfig(Config):
    """Production configuration."""

    DEBUG = False


config = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'default': DevelopmentConfig
}
