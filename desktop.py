import os
import sys
import time
import socket
import threading
import urllib.request
import webbrowser
import traceback

# Setup application log in user Temp folder
LOG_PATH = os.path.join(os.environ.get("TEMP", os.path.expanduser("~")), "filmfreeway_app.log")

def log(msg):
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
    except Exception:
        pass

# Ensure safe stream fallback for windowed / frozen executables where stdout/stderr is None
class SafeStream:
    def write(self, s):
        pass
    def flush(self):
        pass
    def isatty(self):
        return False

if sys.stdout is None:
    sys.stdout = SafeStream()
elif hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

if sys.stderr is None:
    sys.stderr = SafeStream()
elif hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Ensure working directory is set to executable directory in frozen mode
if getattr(sys, "frozen", False):
    exe_dir = os.path.dirname(sys.executable)
    try:
        os.chdir(exe_dir)
    except Exception:
        pass

from app import app
import uvicorn

def is_server_alive(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.15):
            pass
        url = f"http://127.0.0.1:{port}/api/health"
        req = urllib.request.Request(url, headers={"User-Agent": "FilmFreewayDesktop/1.0"})
        with urllib.request.urlopen(req, timeout=0.5) as resp:
            return resp.status == 200
    except Exception:
        return False

def find_available_port(start_port=8000, max_attempts=50):
    for port in range(start_port, start_port + max_attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    return start_port

def open_default_browser(url, port):
    # Poll until server is actively responding to HTTP requests
    log(f"Waiting for server to become ready on port {port}...")
    ready = False
    for i in range(100):
        time.sleep(0.08)
        if is_server_alive(port):
            ready = True
            log(f"Server is healthy and ready after {(i+1)*0.08:.2f}s.")
            break
            
    if not ready:
        log("Server health check timed out, attempting to open browser anyway.")
        
    log(f"Opening user default browser for {url}...")
    opened = False
    try:
        opened = webbrowser.open(url)
        log(f"webbrowser.open returned: {opened}")
    except Exception as e:
        log(f"webbrowser.open exception: {e}")
        
    if not opened:
        try:
            log("Falling back to os.startfile...")
            os.startfile(url)
        except Exception as e:
            log(f"os.startfile exception: {e}")

def main():
    log("=== FilmFreeway DeepSearch Desktop Starting ===")
    
    # 1. Single-instance check: if server is already running, open default browser and exit launcher
    if is_server_alive(8000):
        log("Server is already running on port 8000. Opening default browser.")
        try:
            webbrowser.open("http://127.0.0.1:8000")
        except Exception:
            os.startfile("http://127.0.0.1:8000")
        return

    # 2. Select port
    port = find_available_port(8000)
    app_url = f"http://127.0.0.1:{port}"
    log(f"Target URL: {app_url}")

    # 3. Launch browser opener in background thread once server is ready
    threading.Thread(target=open_default_browser, args=(app_url, port), daemon=True).start()

    # 4. Run Uvicorn directly on the main thread (keeps the process alive indefinitely!)
    try:
        log(f"Starting Uvicorn server on 127.0.0.1:{port}...")
        config = uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            log_level="error",
            log_config=None,
            access_log=False,
        )
        server = uvicorn.Server(config)
        server.install_signal_handlers = lambda: None
        server.run()
    except Exception as e:
        log(f"Uvicorn error: {traceback.format_exc()}")

    log("=== FilmFreeway DeepSearch Desktop Terminated ===")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"Fatal error in main: {traceback.format_exc()}")
