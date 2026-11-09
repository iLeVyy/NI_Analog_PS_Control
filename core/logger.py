"""
core/logger.py
==============
Sets up application logging to a daily file in <root>/logs and to the console.

File name pattern: ps_control_YYYYMMDD.log
Old files are deleted after the retention period configured in settings.json
(logging.retention_days), so the retention is config-driven.

LabVIEW analogy:
    Equivalent to an error/log module that every other module can call.
"""

# Import the standard logging framework.
import logging
import sys
# Import date tools to build the file name and calculate the retention limit.
from datetime import datetime, timedelta
# Import Path for file handling.
from pathlib import Path

from core.activity_trace import ActivityTraceHandler

# Name of the application-wide logger; every module uses get_logger() with this root name.
LOGGER_NAME = "ps_control"
# Prefix of every log file name.
LOG_FILE_PREFIX = "ps_control_"
# Text format of one log line: time, level, module, message.
LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"


def _delete_old_logs(logs_dir: Path, retention_days: int) -> int:
    """
    Delete log files older than the retention period.

    Inputs:
        logs_dir (Path): Folder that contains the log files.
        retention_days (int): Number of days to keep; 0 or less keeps all files.

    Returns:
        int: Number of deleted files.
    """
    # A retention of 0 or less means "keep everything".
    if retention_days <= 0:
        # Nothing deleted.
        return 0
    # Calculate the oldest date that is still kept.
    limit = datetime.now().date() - timedelta(days=retention_days)
    # Counter for deleted files.
    deleted = 0
    # Loop over every file that matches the log name pattern.
    for file in logs_dir.glob(f"{LOG_FILE_PREFIX}*.log"):
        # Take the date part of the file name (text after the prefix).
        date_text = file.stem[len(LOG_FILE_PREFIX):]
        # Try to convert the date text; skip files with an unexpected name.
        try:
            # Parse the date in YYYYMMDD format.
            file_date = datetime.strptime(date_text, "%Y%m%d").date()
        # The name did not contain a valid date.
        except ValueError:
            # Skip this file and continue with the next one.
            continue
        # Delete the file only when it is older than the limit.
        if file_date < limit:
            # Remove the file from disk; ignore it if it is already gone.
            file.unlink(missing_ok=True)
            # Count the deleted file.
            deleted += 1
    # Return how many files were deleted.
    return deleted


def setup_logging(logs_dir: Path, level: str = "INFO", retention_days: int = 30) -> Path:
    """
    Configure the application logger (file + console) and clean up old logs.

    Inputs:
        logs_dir (Path): Folder for the log files (created if missing).
        level (str): Log level name, e.g. "DEBUG", "INFO", "WARNING".
        retention_days (int): Days to keep old log files.

    Returns:
        Path: Path of today's log file.
    """
    # Make sure the logs folder exists.
    logs_dir.mkdir(parents=True, exist_ok=True)
    # Build today's log file path.
    log_file = logs_dir / f"{LOG_FILE_PREFIX}{datetime.now():%Y%m%d}.log"
    # Get the application root logger.
    logger = logging.getLogger(LOGGER_NAME)
    # Convert the level text to a logging constant; fall back to INFO if unknown.
    file_level = getattr(logging, str(level).upper(), logging.INFO)
    logger.setLevel(min(file_level, logging.INFO))
    # Remove handlers from a previous setup so lines are not written twice.
    for handler in list(logger.handlers):
        # Close the old handler to release the file.
        handler.close()
        # Detach the old handler from the logger.
        logger.removeHandler(handler)
    # Create the shared line formatter.
    formatter = logging.Formatter(LOG_FORMAT)
    # Create a handler that appends to today's file in UTF-8.
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(file_level)
    # Apply the line format to the file handler.
    file_handler.setFormatter(formatter)
    # Attach the file handler.
    logger.addHandler(file_handler)
    # Create a handler that prints to the console (useful in development / debug build).
    if sys.stderr is not None:
        console_handler = logging.StreamHandler()
        console_handler.setLevel(file_level)
        # Apply the same line format to the console handler.
        console_handler.setFormatter(formatter)
        # Attach the console handler.
        logger.addHandler(console_handler)
    # Copy existing log messages into the bounded visual trace.
    trace_handler = ActivityTraceHandler()
    trace_handler.setLevel(logging.INFO)
    logger.addHandler(trace_handler)
    # Do not pass messages up to Python's global root logger (avoids duplicates).
    logger.propagate = False
    # Delete log files older than the retention period.
    deleted = _delete_old_logs(logs_dir, retention_days)
    # Record how many old files were removed.
    logger.debug("Log retention: %d old file(s) deleted", deleted)
    # Return today's log file path to the caller.
    return log_file


def get_logger(module_name: str) -> logging.Logger:
    """
    Get a child logger for one module, e.g. get_logger("config_manager").

    Inputs:
        module_name (str): Short name of the calling module.

    Returns:
        logging.Logger: Logger named "ps_control.<module_name>".
    """
    # Child loggers inherit the handlers of the application root logger.
    return logging.getLogger(f"{LOGGER_NAME}.{module_name}")
