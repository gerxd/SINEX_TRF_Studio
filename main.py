import sys

from PyQt5.QtWidgets import QApplication

from sinex_parser.ui.main_window import SINEXParserApp


def main() -> None:
    app = QApplication(sys.argv)
    parser_app = SINEXParserApp()
    parser_app.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
