"""Console entry point: python -m markwell.cli_checker

Reconciles history.json against Google Drive. The GUI exposes the same thing
as the "동기화 점검" tab.
"""

from . import config as config_mod, configure_console_encoding
from .checker_service import check_sync


def main() -> None:
    configure_console_encoding()
    print("----------------------------------------------------------------")
    print("Google Drive Sync Checker")
    print("----------------------------------------------------------------")
    cfg = config_mod.load_config()
    check_sync(cfg, log=print)


if __name__ == "__main__":
    main()
