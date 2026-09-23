import os
import sys
import subprocess
import shutil

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

def build():
    print("=================================================================")
    print("   CRÉATION DU LOGICIEL EXÉCUTABLE STANDALONE SANS PYTHON        ")
    print("=================================================================")

    # Verify PyInstaller is installed
    try:
        import PyInstaller
    except ImportError:
        print("Installation de PyInstaller...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller"])

    # Base paths
    project_dir = os.path.dirname(os.path.abspath(__file__))
    dist_dir = os.path.join(project_dir, "dist")
    build_dir = os.path.join(project_dir, "build")

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--onedir",
        "--windowed", # No console prompt window, completely silent desktop app!
        "--name", "FilmFreewayDeepSearch",
        "--add-data", f"{os.path.join(project_dir, 'static')};static",
        "--add-data", f"{os.path.join(project_dir, 'filmfreeway.db')};.",
        "--add-data", f"{os.path.join(project_dir, 'profile.json')};.",
        "--hidden-import", "uvicorn",
        "--hidden-import", "uvicorn.logging",
        "--hidden-import", "uvicorn.loops",
        "--hidden-import", "uvicorn.loops.auto",
        "--hidden-import", "uvicorn.protocols",
        "--hidden-import", "uvicorn.protocols.http",
        "--hidden-import", "uvicorn.protocols.http.auto",
        "--hidden-import", "uvicorn.protocols.websockets",
        "--hidden-import", "uvicorn.protocols.websockets.auto",
        "--hidden-import", "uvicorn.lifespan",
        "--hidden-import", "uvicorn.lifespan.on",
        "--hidden-import", "webview",
        "--hidden-import", "webview.platforms.winforms",
        "--hidden-import", "clr_loader",
        "--hidden-import", "pythonnet",
        "--hidden-import", "openpyxl",
        "--hidden-import", "psutil",
        "desktop.py"
    ]

    print("Exécution de PyInstaller...")
    ret = subprocess.call(cmd)
    if ret != 0:
        print("[ERREUR] Échec de la compilation PyInstaller.")
        sys.exit(ret)

    output_exe = os.path.join(dist_dir, "FilmFreewayDeepSearch", "FilmFreewayDeepSearch.exe")
    if os.path.exists(output_exe):
        print(f"\n[SUCCÈS] Exécutable autonome généré :")
        print(f" -> {output_exe}")
        print("Ce dossier contient TOUT (runtime Python, bibliothèques, base de données).")
        print("Il fonctionne sur n'importe quel PC Windows sans avoir besoin d'installer Python !")
    else:
        print("[ERREUR] L'exécutable n'a pas été trouvé dans le dossier de sortie.")
        sys.exit(1)

if __name__ == "__main__":
    build()
