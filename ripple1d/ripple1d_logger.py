"""Logging utility an setup."""

import inspect
import json
import logging
import os
import time
import traceback
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler

from ripple1d.consts import SUPPRESS_LOGS


class RippleLogFormatter(logging.Formatter):
    """Format log messages as JSON-LD."""

    def __init__(
        self, datefmt="%Y-%m-%dT%H:%M:%SZ", include_traceback: bool = True, include_fields: list = None, **kwargs
    ):
        """Initialize the formatter with options."""
        super().__init__(datefmt=datefmt, **kwargs)
        self.include_traceback = include_traceback

        # List of valid field options
        self.include_fields_options = [
            "logger_name",
            "function_name",
            "line_number",
            "filename",
            "thread_name",
            "process_name",
            "process_id",
            "error",
            "traceback",
        ]

        # Validate and set include_fields
        if include_fields is None:
            self.include_fields = []
        else:
            self.include_fields = [field for field in include_fields if field in self.include_fields_options]

    def format(self, record):
        """Set up fine-grain format and logging options."""
        # Get the function name of the caller
        stack = inspect.stack()
        for frame_info in stack[1:]:
            if frame_info.function != "log":
                record.funcName = frame_info.function
                break

        # Basic log entry fields that are always included
        log_entry = {
            "@type": "RippleLogs",
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "msg": record.getMessage(),
        }

        # Conditionally add fields based on the include_fields list
        if "logger_name" in self.include_fields:
            log_entry["logger_name"] = record.name
        if "function_name" in self.include_fields:
            log_entry["function_name"] = record.funcName
        if "line_number" in self.include_fields:
            log_entry["line_number"] = record.lineno
        if "filename" in self.include_fields:
            log_entry["filename"] = record.pathname
        if "thread_name" in self.include_fields:
            log_entry["thread_name"] = record.threadName
        if "process_name" in self.include_fields:
            log_entry["process_name"] = record.processName
        if "process_id" in self.include_fields:
            log_entry["process_id"] = record.process
        if "error" in self.include_fields:
            log_entry["error"] = None
        if "traceback" in self.include_fields and record.exc_info and self.include_traceback:
            log_entry["error"] = str(record.exc_info[1])
            log_entry["traceback"] = traceback.format_exc()

        return json.dumps(log_entry)


def configure_logging(level, logfile: str = None, milliseconds: bool = False, verbose: bool = False):
    """Configure logging for ripple1d."""
    if milliseconds:
        datefmt = "%Y-%m-%dT%H:%M:%S.%fZ"
    else:
        datefmt = "%Y-%m-%dT%H:%M:%SZ"

    if verbose:
        include_fields = [
            "logger_name",
            "function_name",
            "line_number",
            "filename",
            "thread_name",
            "process_name",
            "process_id",
        ]
    else:
        include_fields = []

    log_formatter = RippleLogFormatter(datefmt=datefmt, include_traceback=True, include_fields=include_fields)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(log_formatter)
    handlers = [console_handler]

    if logfile:
        file_handler = RotatingFileHandler(logfile, maxBytes=5000000, backupCount=5)
        file_handler.setFormatter(log_formatter)
        handlers.append(file_handler)

    for module in SUPPRESS_LOGS:
        logging.getLogger(module).setLevel(logging.WARNING)

    logging.basicConfig(
        level=level,
        handlers=handlers,
    )


def initialize_server_logger(log_dir: str = "", log_level: int = logging.INFO) -> logging.Logger:
    """Initialize log with JSON-LD style formatting and throttled level for AWS libs.

    By default sends to StreamHandler (stdout/stderr), but can provide a filename to log to disk instead.
    """
    filename = os.path.join(log_dir, "server-logs.jsonld")

    for module in SUPPRESS_LOGS:
        logging.getLogger(module).setLevel(logging.ERROR)

    log = logging.getLogger()
    log.setLevel(log_level)
    formatter = RippleLogFormatter()

    if filename:
        print(f"Initializing log file: {filename}")
        os.makedirs(os.path.dirname(os.path.abspath(filename)), exist_ok=True)
        file_handler = logging.FileHandler(filename=filename)
        file_handler.setFormatter(formatter)
        log.addHandler(file_handler)
    else:
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)
        log.addHandler(stream_handler)

    return log
