# registrar.py
# Installiert / lädt die begleitenden Skripte:
#  - Linienverdraengung.py           -> <Profil>\python\expressions\
#  - Netzfragmente_verknuepfen.py    -> <Profil>\processing\scripts\    (ohne "python")
#
# Außerdem: Sitzungsimport (sys.path-Anpassung + import), Deinstallation,
#           sowie ein einfaches Log auf den Desktop.

import os
import sys
import shutil
from datetime import datetime
from .i18n import t  # <-- zweisprachige Texte

# ----------------- Logging auf den Desktop (gemeinsame Datei) -----------------
REG_LOG_ENABLED = False  # wird von main.py per set_logging_enabled() gesetzt

def set_logging_enabled(flag: bool):
    """Von außen aufgerufen (main.py), um Logging an/aus zu schalten."""
    global REG_LOG_ENABLED
    REG_LOG_ENABLED = bool(flag)

def _logfile_path() -> str:
    home = os.path.expanduser("~")
    return os.path.join(home, "Desktop", "line_displacement.log")

def _log(msg: str):
    # immer in die Python-Konsole
    print(f"[registrar] {msg}")
    if not REG_LOG_ENABLED:
        return
    try:
        with open(_logfile_path(), "a", encoding="utf-8") as f:
            f.write(f"[registrar] {datetime.now().isoformat(timespec='seconds')}  {msg}\n")
    except Exception:
        pass

# ----------------- Profilpfade ermitteln -----------------
def qgis_profile_root() -> str | None:
    """
    Versucht die Profilwurzel zu bestimmen, z. B. unter Windows:
      C:\\Users\\…\\AppData\\Roaming\\QGIS\\QGIS3\\profiles\\default
    """
    home = os.path.expanduser("~")
    candidates = [
        # Windows
        os.path.join(home, "AppData", "Roaming", "QGIS", "QGIS3", "profiles", "default"),
        # Linux
        os.path.join(home, ".local", "share", "QGIS", "QGIS3", "profiles", "default"),
        # macOS
        os.path.join(home, "Library", "Application Support", "QGIS", "QGIS3", "profiles", "default"),
    ]
    for c in candidates:
        if os.path.isdir(c):
            return c
    return None

def qgis_user_python_dir() -> str | None:
    """
    <Profil>\\python – für expressions.
    """
    root = qgis_profile_root()
    if not root:
        return None
    pydir = os.path.join(root, "python")
    return pydir if os.path.isdir(pydir) or True else None  # wir legen ihn bei Bedarf an

# ----------------- Installation / Import / Deinstallation -----------------
def install_scripts_permanent(plugin_dir: str):
    """
    Kopiert:
      - Linienverdraengung.py           -> <Profil>\\python\\expressions\\
      - Netzfragmente_verknuepfen.py    -> <Profil>\\processing\\scripts\\    (ohne "python")
    und lädt beide Module für die laufende Sitzung.
    """
    try:
        root = qgis_profile_root()
        user_py = qgis_user_python_dir()
        if not root or not user_py:
            return (False, t("Profilpfade konnten nicht ermittelt werden.",
                             "Could not determine profile paths."))

        expr_dir = os.path.join(user_py, "expressions")
        proc_dir = os.path.join(root, "processing", "scripts")  # << einzig maßgeblicher Pfad

        os.makedirs(expr_dir, exist_ok=True)
        os.makedirs(proc_dir, exist_ok=True)

        src_expr = os.path.join(plugin_dir, "scripts", "Linienverdraengung.py")
        src_proc = os.path.join(plugin_dir, "scripts", "Netzfragmente_verknuepfen.py")

        shutil.copy2(src_expr, os.path.join(expr_dir, "Linienverdraengung.py"))
        _log(t(f"Kopiert -> {os.path.join(expr_dir, 'Linienverdraengung.py')}",
               f"copied -> {os.path.join(expr_dir, 'Linienverdraengung.py')}"))

        shutil.copy2(src_proc, os.path.join(proc_dir, "Netzfragmente_verknuepfen.py"))
        _log(t(f"Kopiert -> {os.path.join(proc_dir, 'Netzfragmente_verknuepfen.py')}",
               f"copied -> {os.path.join(proc_dir, 'Netzfragmente_verknuepfen.py')}"))

        ok, msg = import_scripts_session(plugin_dir)

        info_de = f"expr_dir = {expr_dir}\nproc_dir = {proc_dir}\n{msg}"
        info_en = f"expr_dir = {expr_dir}\nproc_dir = {proc_dir}\n{msg}"
        return (True, t(info_de, info_en))
    except Exception as e:
        return (False, t(f"Fehler beim Kopieren/Installieren: {e}",
                         f"Error copying/installing: {e}"))

def import_scripts_session(plugin_dir: str):
    """
    Fügt die Plugin-Skripte (Ordner 'scripts') dem sys.path hinzu und importiert beide Module.
    Das ist unabhängig von der dauerhaften Installation und betrifft nur die laufende Sitzung.
    """
    try:
        expr_path = os.path.join(plugin_dir, "scripts")
        if expr_path not in sys.path:
            sys.path.insert(0, expr_path)
            _log(f"sys.path += {expr_path}")

        import importlib

        try:
            import Linienverdraengung
            importlib.reload(Linienverdraengung)
            _log(t("Neuladen(Linienverdraengung) OK", "reload(Linienverdraengung) OK"))
        except Exception:
            import Linienverdraengung  # noqa: F401
            _log(t("Import Linienverdraengung OK", "import Linienverdraengung OK"))

        try:
            import Netzfragmente_verknuepfen
            importlib.reload(Netzfragmente_verknuepfen)
            _log(t("Neuladen(Netzfragmente_verknuepfen) OK", "reload(Netzfragmente_verknuepfen) OK"))
        except Exception:
            import Netzfragmente_verknuepfen  # noqa: F401
            _log(t("Import Netzfragmente_verknuepfen OK", "import Netzfragmente_verknuepfen OK"))

        return (True, t(
            "Sitzungsimport: Linienverdraengung: OK (Linienverdraengung); Netzfragmente: OK (Netzfragmente_verknuepfen)",
            "Session import: Linienverdraengung: OK (Linienverdraengung); Fragments: OK (Netzfragmente_verknuepfen)"
        ))
    except Exception as e:
        return (False, t(f"Importfehler: {e}", f"Import error: {e}"))

def uninstall_scripts(remove_expr: bool = True, remove_proc: bool = True):
    """
    Entfernt die dauerhaft installierten Dateien soweit möglich aus den **einzigen** Zielorten:
      - Expressions: <Profil>\\python\\expressions\\Linienverdraengung.py
      - Processing : <Profil>\\processing\\scripts\\Netzfragmente_verknuepfen.py
    """
    removed = []
    errors = []
    try:
        root = qgis_profile_root()
        user_py = qgis_user_python_dir()
        if not root or not user_py:
            return (False, t("Pfadermittlung fehlgeschlagen.",
                             "Path detection failed."))

        if remove_expr:
            expr_path = os.path.join(user_py, "expressions", "Linienverdraengung.py")
            if os.path.isfile(expr_path):
                try:
                    os.remove(expr_path)
                    removed.append(expr_path)
                except Exception as e:
                    errors.append(f"{expr_path}: {e}")

        if remove_proc:
            proc_path = os.path.join(root, "processing", "scripts", "Netzfragmente_verknuepfen.py")
            if os.path.isfile(proc_path):
                try:
                    os.remove(proc_path)
                    removed.append(proc_path)
                except Exception as e:
                    errors.append(f"{proc_path}: {e}")

        ok = (len(errors) == 0)
        if removed:
            msg_de = "Entfernt:\n" + "\n".join(removed)
            msg_en = "Removed:\n" + "\n".join(removed)
        else:
            msg_de = "Nichts zu entfernen."
            msg_en = "Nothing to remove."
        if errors:
            msg_de += "\nFehler:\n" + "\n".join(errors)
            msg_en += "\nErrors:\n" + "\n".join(errors)
        return (ok, t(msg_de, msg_en))
    except Exception as e:
        return (False, t(f"Fehler beim Entfernen: {e}",
                         f"Error removing files: {e}"))
