"""Console entry point: python -m markwell.cli_converter

Kept for development and scripted runs; the packaged app uses the GUI instead.
"""

from . import config as config_mod, configure_console_encoding
from .converter_service import process_files


def main() -> None:
    configure_console_encoding()
    print("----------------------------------------------------------------")
    print("MarkItDown Batch Converter")
    print("----------------------------------------------------------------")
    cfg = config_mod.load_config()
    summary = process_files(cfg, log=print)
    raise SystemExit(1 if summary.failed else 0)


if __name__ == "__main__":
    main()
