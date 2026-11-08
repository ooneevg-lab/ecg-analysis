"""
Entry point for the ECG Analysis Flask application.
"""
import sys
import threading
import webbrowser
import time
import signal
import atexit
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from app import create_app

URL = 'http://127.0.0.1:5000'


def open_browser():
    """ИСПРАВЛЕНО: автооткрытие браузера через 1.5 сек после старта сервера."""
    time.sleep(1.5)
    webbrowser.open(URL)


def shutdown_hook():
    """Graceful shutdown: stop watcher, persist session state."""
    print("\n" + "=" * 50)
    print("Завершение работы сервера...")
    watcher = app.extensions.get('watcher')
    if watcher:
        try:
            watcher.stop()
            print("Watcher остановлен")
        except Exception as e:
            print(f"Ошибка остановки watcher: {e}")
    try:
        from app.routes.upload import persist_all_records
        persist_all_records()
        print("Состояние сессии сохранено")
    except Exception as e:
        print(f"Ошибка сохранения состояния: {e}")
    print("=" * 50)


def signal_handler(signum, frame):
    """Handle SIGTERM/SIGINT for graceful shutdown."""
    print(f"\nПолучен сигнал {signum}, завершение работы...")
    shutdown_hook()
    sys.exit(0)


app = create_app()

# Register signal handlers
signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)
atexit.register(shutdown_hook)

if __name__ == '__main__':
    # Автооткрытие браузера (в отдельном потоке, чтобы не блокировать сервер)
    threading.Thread(target=open_browser, daemon=True).start()

    print("=" * 50)
    print("Запуск сервера анализа ЭКГ...")
    print("Браузер откроется автоматически...")
    print(f"Если нет — откройте: {URL}")
    print("=" * 50)
    app.run(host='127.0.0.1', port=5000, debug=False)
