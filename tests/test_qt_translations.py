import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication, QLineEdit

from saywell import ui


def test_the_right_click_menu_speaks_chinese():
    app = QApplication.instance() or QApplication([])
    try:
        assert ui.load_translations()
        edit = QLineEdit()
        menu = edit.createStandardContextMenu()
        words = [action.text().split("\t")[0] for action in menu.actions() if action.text()]
        assert "粘贴(&P)" in words and "复制(&C)" in words
        assert ui.load_translations()  # Loading twice keeps the one translator.
        assert len(ui._translators) == 1
    finally:
        for translator in ui._translators:
            QCoreApplication.removeTranslator(translator)
        ui._translators.clear()
    assert app
