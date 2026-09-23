import os
import sys
import time
import socket
import threading
import urllib.request
import webbrowser
import subprocess
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

def run_uvicorn_server(port):
    try:
        log(f"Demarrage du serveur Uvicorn sur 127.0.0.1:{port}...")
        config = uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            log_level="error",
            log_config=None,  # Empêche le plantage sur sys.stdout.isatty en mode sans console
            access_log=False,
        )
        server = uvicorn.Server(config)
        server.install_signal_handlers = lambda: None
        server.run()
    except Exception as e:
        log(f"Erreur Uvicorn : {traceback.format_exc()}")

def open_app_window(app_url):
    """
    Ouvre l'application dans une vraie fenêtre logicielle de bureau
    sans barre d'adresse URL ni onglets de navigation.
    """
    log(f"Ouverture de l'interface pour {app_url}...")
    
    # Répertoire de données dédié pour créer une instance de fenêtre autonome
    user_data_dir = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "FilmFreewayDeepSearch", "app_profile")
    try:
        os.makedirs(user_data_dir, exist_ok=True)
    except Exception:
        pass

    # 1. Priorité absolue : Fenêtre d'application dédiée Microsoft Edge ou Google Chrome
    # (Mode --app= qui retire 100% de la barre d'adresse et transforme la page web en vrai logiciel desktop)
    browser_candidates = [
        os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
    ]

    for browser_path in browser_candidates:
        if os.path.exists(browser_path):
            try:
                log(f"Lancement de la fenetre application avec : {browser_path}")
                args = [
                    browser_path,
                    f"--app={app_url}",
                    f"--user-data-dir={user_data_dir}",
                    "--window-size=1380,900",
                    "--no-first-run",
                    "--no-default-browser-check"
                ]
                proc = subprocess.Popen(args)
                return proc
            except Exception as e:
                log(f"Echec lancement {browser_path} : {e}")

    # 2. Alternative : PyWebView si aucun navigateur n'a répondu
    try:
        import webview
        log("Tentative de lancement avec pywebview...")
        window = webview.create_window(
            title="FilmFreeway DeepSearch Pro",
            url=app_url,
            width=1380,
            height=900,
            min_size=(1024, 680),
            text_select=True,
            confirm_close=False,
        )
        webview.start()
        return None
    except Exception as e:
        log(f"pywebview non disponible : {e}")

    # 3. Dernier recours garanti : Navigateur par défaut du système
    log("Lancement du navigateur par défaut...")
    webbrowser.open(app_url)
    return None

def main():
    log("=== FilmFreeway DeepSearch Desktop Demarre ===")
    
    # 1. Vérification d'instance unique : si le serveur tourne déjà sur 8000, ouvrir l'interface immédiatement
    if is_server_alive(8000):
        log("Serveur deja actif sur le port 8000. Ouverture immediate de l'interface.")
        open_app_window("http://127.0.0.1:8000")
        return

    # 2. Choix d'un port libre
    port = find_available_port(8000)
    log(f"Port selectionne : {port}")

    # 3. Démarrage du serveur dans un thread en arrière-plan
    server_thread = threading.Thread(target=run_uvicorn_server, args=(port,), daemon=True)
    server_thread.start()

    # 4. Attente active que le serveur soit prêt (test de santé ultra-rapide)
    ready = False
    for i in range(50):
        time.sleep(0.08)
        if is_server_alive(port):
            ready = True
            log(f"Serveur pret et operationnel apres {(i+1)*0.08:.2f}s !")
            break

    if not ready:
        log("Attention: le serveur n'a pas repondu a /api/health dans les 4s, tentative d'ouverture quand meme.")

    app_url = f"http://127.0.0.1:{port}"

    # 5. Ouverture de l'interface desktop
    proc = open_app_window(app_url)

    # 6. Maintien du processus en vie tant que la fenêtre tourne
    if proc is not None:
        try:
            proc.wait()
            log("Fenetre applicative fermee par l'utilisateur.")
        except Exception:
            pass
    else:
        try:
            while True:
                time.sleep(1)
        except (KeyboardInterrupt, SystemExit):
            pass

    log("=== Fermeture propre de l'application ===")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"Erreur fatale : {traceback.format_exc()}")
