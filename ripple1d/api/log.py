# """API Lofging module for Ripple1D."""

# import inspect
# import json
# import logging
# import os
# import time
# import traceback
# from datetime import datetime, timezone

# from ripple1d.consts import SUPPRESS_LOGS
# from ripple1d.ripple1d_logger import RippleLogFormatter


# def initialize_log(log_dir: str = "", log_level: int = logging.INFO) -> logging.Logger:
#     """Initialize log with JSON-LD style formatting and throttled level for AWS libs.

#     By default sends to StreamHandler (stdout/stderr), but can provide a filename to log to disk instead.
#     """
#     filename = os.path.join(log_dir, f"{_get_log_filename_suffix()}.jsonld")

#     for module in SUPPRESS_LOGS:
#         logging.getLogger(module).setLevel(logging.ERROR)

#     log = logging.getLogger()
#     log.setLevel(log_level)
#     formatter = RippleLogFormatter()

#     if filename:
#         print(f"Initializing log file: {filename}")
#         os.makedirs(os.path.dirname(os.path.abspath(filename)), exist_ok=True)
#         file_handler = logging.FileHandler(filename=filename)
#         file_handler.setFormatter(formatter)
#         log.addHandler(file_handler)
#     else:
#         stream_handler = logging.StreamHandler()
#         stream_handler.setFormatter(formatter)
#         log.addHandler(stream_handler)

#     return log


# def _get_log_filename_suffix():
#     stack_filenames = [frame.filename for frame in inspect.stack()]

#     # Check for specific patterns in the stack filenames
#     if any("huey" in filename for filename in stack_filenames):
#         return "huey"
#     elif any("flask" in filename for filename in stack_filenames):
#         return "flask"
#     else:
#         raise ValueError(
#             f"Could not determine if process invoked by huey or by flask. Stack filenames: {stack_filenames}"
#         )
