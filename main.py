import multiprocessing
import sys
from pathlib import Path

from sinex_parser import bootstrap


def main() -> None:
    code = bootstrap.prepare(sys.argv[1:])
    if code is None:
        from PyQt6.QtWidgets import QApplication

        from sinex_parser.io import library
        library.PROGRAM_DIR = Path(sys.argv[0]).resolve().parent
        from sinex_parser.ui.main_window import SINEXParserApp

        app = QApplication(sys.argv)
        parser_app = SINEXParserApp()
        parser_app.show()
        code = app.exec()
    sys.exit(code)


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
