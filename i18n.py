# i18n.py – einfache Sprachhilfe
from qgis.PyQt.QtCore import QSettings

def t(de: str, en: str) -> str:
    """Gibt deutschen oder englischen Text zurück, abhängig von der QGIS-Sprache."""
    lang = (QSettings().value('locale/userLocale', 'en') or 'en')[:2].lower()
    return de if lang == 'de' else en
