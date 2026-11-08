"""
Desktop launcher for ECG Analysis application.

Features:
- PyWebView native window wrapping Flask app
- System tray icon with minimize-to-tray and restore
- Graceful shutdown (watcher, session persistence)
"""
import os
import sys
import threading
import time
import signal
import atexit
from pathlib import Path

if getattr(sys, 'frozen', False):
    sys.path.insert(0, str(Path(sys._MEIPASS)))
else:
    sys.path.insert(0, str(Path(__file__).parent.parent))

flask_app = None
tray_icon = None
webview_window = None
_quitting = False
_shutdown_done = False


def run_flask_server():
    global flask_app
    from app import create_app
    flask_app = create_app('production')
    flask_app.run(host='127.0.0.1', port=5000, debug=False, use_reloader=False)


def wait_for_server(url: str, timeout: int = 30):
    import urllib.request
    start = time.time()
    while time.time() - start < timeout:
        try:
            urllib.request.urlopen(url)
            return True
        except Exception:
            time.sleep(0.1)
    return False


def shutdown_hook():
    global tray_icon, _shutdown_done
    if _shutdown_done:
        return
    _shutdown_done = True
    print("\n" + "=" * 50)
    print("Завершение работы сервера...")
    if tray_icon:
        try:
            tray_icon.stop()
        except Exception:
            pass
        tray_icon = None
    if flask_app:
        watcher = flask_app.extensions.get('watcher')
        if watcher:
            try:
                watcher.stop()
                print("Watcher остановлен")
            except Exception as e:
                print(f"Ошибка остановки watcher: {e}")
        try:
            with flask_app.app_context():
                from app.routes.upload import persist_all_records
                persist_all_records()
                print("Состояние сессии сохранено")
        except Exception as e:
            print(f"Ошибка сохранения состояния: {e}")
    print("=" * 50)


def signal_handler(signum, frame):
    print(f"\nПолучен сигнал {signum}, завершение работы...")
    shutdown_hook()
    sys.exit(0)


def create_tray_icon_image():
    from PIL import Image, ImageDraw
    size = 64
    img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle(
        [4, 4, size - 4, size - 4],
        radius=12,
        fill=(59, 130, 246),
        outline=(37, 99, 235),
        width=2,
    )
    draw.text((size // 2, size // 2), "Э", fill="white",
              anchor="mm")
    return img


def on_show_window(icon, item):
    global webview_window
    if webview_window:
        try:
            webview_window.show()
        except Exception:
            pass


def on_quit_app(icon, item):
    global _quitting
    _quitting = True
    shutdown_hook()
    try:
        import webview
        webview.stop()
    except Exception:
        pass
    threading.Timer(2.0, lambda: os._exit(0)).start()


def start_tray():
    global tray_icon
    try:
        import pystray
    except ImportError:
        print("pystray не установлен. Трей недоступен.")
        return

    image = create_tray_icon_image()
    menu = pystray.Menu(
        pystray.MenuItem("Показать окно", on_show_window, default=True),
        pystray.MenuItem("Выход", on_quit_app),
    )
    tray_icon = pystray.Icon(
        "ECG_Analysis",
        image,
        "Анализ ЭКГ",
        menu,
    )
    tray_icon.run()


def start_periodic_persist(interval=10):
    """Background thread that persists session records every `interval` seconds.

    This ensures that even if the process is killed (e.g. via Task Manager),
    at most `interval` seconds of session data are lost instead of everything.
    """
    def _loop():
        while not _quitting:
            time.sleep(interval)
            if _quitting:
                break
            if flask_app:
                try:
                    with flask_app.app_context():
                        from app.routes.upload import persist_all_records
                        persist_all_records()
                except Exception:
                    pass
    t = threading.Thread(target=_loop, daemon=True)
    t.start()


def main():
    global webview_window

    try:
        import webview
    except ImportError:
        print("PyWebView не установлен. Установите: pip install pywebview")
        sys.exit(1)

    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    atexit.register(shutdown_hook)

    server_thread = threading.Thread(target=run_flask_server, daemon=True)
    server_thread.start()

    url = 'http://127.0.0.1:5000'
    print("Запуск сервера...")
    if not wait_for_server(url):
        print("Ошибка: сервер не запустился")
        sys.exit(1)

    print(f"Сервер запущен: {url}")

    tray_thread = threading.Thread(target=start_tray, daemon=True)
    tray_thread.start()
    time.sleep(0.3)

    start_periodic_persist()

    def on_closing():
        global webview_window
        if _quitting:
            return True
        if webview_window:
            try:
                webview_window.hide()
            except Exception:
                pass
        if tray_icon:
            try:
                tray_icon.notify("Приложение свёрнуто в трей", "Анализ ЭКГ")
            except Exception:
                pass
        return False

    webview_window = webview.create_window(
        title='Анализ ЭКГ',
        url=url,
        width=1280,
        height=800,
        min_size=(800, 600),
        resizable=True,
        background_color='#f9fafb',
    )
    webview_window.events.closing += on_closing

    webview.start(debug=False)


if __name__ == '__main__':
    main()
