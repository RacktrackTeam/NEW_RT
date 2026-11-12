"""
Centralized logging configuration.

Usage in any entry point:
    from logging_config import setup_logging
    setup_logging(verbose=False)

Logs go to stderr in human-friendly format. Set SWITCH_AGENT_LOG_FILE env
var to also tee to a file.
"""
import logging
import os
import sys
from pathlib import Path


def setup_logging(verbose: bool = False, log_file: str | None = None):
    """
    Configure root logger and project loggers.
    Idempotent - safe to call multiple times.
    """
    level = logging.DEBUG if verbose else logging.INFO

    fmt = "%(asctime)s %(levelname)-5s %(name)-15s %(message)s"
    datefmt = "%H:%M:%S"

    handlers: list[logging.Handler] = []

    stream = logging.StreamHandler(sys.stderr)
    stream.setFormatter(logging.Formatter(fmt, datefmt))
    handlers.append(stream)

    log_file = log_file or os.environ.get("SWITCH_AGENT_LOG_FILE")
    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(log_file)
        fh.setFormatter(logging.Formatter(fmt, datefmt))
        handlers.append(fh)

    # Clear existing handlers on the root logger to avoid duplicates
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    for h in handlers:
        root.addHandler(h)
    root.setLevel(level)

    # Quiet noisy third-party libs
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("requests").setLevel(logging.WARNING)
    logging.getLogger("pdfminer").setLevel(logging.ERROR)
    logging.getLogger("pdfplumber").setLevel(logging.WARNING)
