"""Entry point for the packaged GUI.

PyInstaller needs a plain script to analyze; ``markwell.gui.app`` uses relative
imports and can only be run as a module, so this thin shim bridges the two.
Running ``python main.py`` from a source checkout works identically.

``--selftest <file>...`` converts files and prints the result without opening a
window. A packaged build cannot be exercised any other way without clicking
through the GUI, and it is exactly the packaged build where missing bundled
modules and DLLs show up.
"""

import sys


def _selftest(paths: list) -> int:
    from pathlib import Path

    from markwell import dispatch, paths as path_mod
    from markwell.config import load_config
    from markwell.ocr.factory import get_ocr_engine

    print(f"frozen        : {path_mod.is_frozen()}")
    print(f"bundle dir    : {path_mod.bundle_dir()}")
    print(f"user data dir : {path_mod.user_data_dir()}")

    cfg = load_config()
    engine = get_ocr_engine("tesseract", log=lambda m: print(f"  {m}"))
    print(f"tesseract     : {engine.binary_path() if engine.is_available() else 'NOT FOUND'}")
    print(f"ocr lang      : {cfg.ocr_lang}")

    failures = 0
    for raw in paths:
        target = Path(raw)
        print(f"\n--- {target.name} ---")
        if not target.exists():
            print("  missing file")
            failures += 1
            continue
        try:
            result = dispatch.convert_file(
                target, ocr_lang=cfg.ocr_lang, ocr_engine=engine, log=lambda m: print(f"  {m}")
            )
            print(f"  engine={result.engine} ocr_pages={result.ocr_pages} chars={len(result.markdown)}")
        except Exception as e:
            print(f"  FAILED: {type(e).__name__}: {e}")
            failures += 1

    print(f"\nselftest {'FAILED' if failures else 'OK'} ({failures} failure(s))")
    return 1 if failures else 0


def _selftest_pipeline(workdir: str) -> int:
    """Run the full batch pipeline against a throwaway workspace.

    ``--selftest`` only exercises the conversion engines; this covers what
    happens around them -- which originals get archived, what lands in the
    output folder, duplicate detection. Google Drive and Notion are forced off
    and every path -- including the user data directory that holds
    history.json -- is redirected under ``workdir``, so a test run neither
    reads nor writes the user's real config, credentials or conversion history.
    """
    from pathlib import Path

    from markwell import paths as path_mod

    root = Path(workdir)

    # Must happen before config/history are imported-and-used: they resolve
    # their file locations through paths.user_data_dir() at call time. Without
    # this a test run reads the real history.json -- silently reporting real
    # documents as "duplicates" -- and writes its own junk entries back into it.
    sandbox_data = root / "_selftest_userdata"
    sandbox_data.mkdir(parents=True, exist_ok=True)
    path_mod.user_data_dir = lambda: sandbox_data

    from markwell.config import AppConfig
    from markwell.converter_service import process_files

    cfg = AppConfig(
        enable_drive=False,
        enable_notion=False,
        input_dir=str(root / "input_files"),
        processed_dir=str(root / "processed_files"),
        output_dir=str(root / "output_files"),
    )

    queue_dir = root / "queue_source"
    queued = sorted(p for p in queue_dir.iterdir() if p.is_file()) if queue_dir.is_dir() else []

    if queued:
        print(f"### QUEUE MODE ({len(queued)} file(s))")
        s = process_files(cfg, files=queued, log=lambda m: print(f"  {m}"))
        print(f"@@ queue converted={s.converted} duplicates={s.duplicates} "
              f"skipped={s.skipped} failed={s.failed}")

    print("### SCAN MODE (input_files)")
    s = process_files(cfg, log=lambda m: print(f"  {m}"))
    print(f"@@ scan converted={s.converted} duplicates={s.duplicates} "
          f"skipped={s.skipped} failed={s.failed}")

    for name in ("queue_source", "input_files", "processed_files", "output_files"):
        d = root / name
        entries = sorted(p.name for p in d.iterdir()) if d.is_dir() else []
        print(f"@@ {name}: {entries}")

    return 0


def main() -> None:
    from markwell import configure_console_encoding

    configure_console_encoding()

    if "--selftest-pipeline" in sys.argv:
        index = sys.argv.index("--selftest-pipeline")
        raise SystemExit(_selftest_pipeline(sys.argv[index + 1]))

    if "--selftest" in sys.argv:
        index = sys.argv.index("--selftest")
        raise SystemExit(_selftest(sys.argv[index + 1:]))

    from markwell.gui.app import main as gui_main

    gui_main()


if __name__ == "__main__":
    main()
