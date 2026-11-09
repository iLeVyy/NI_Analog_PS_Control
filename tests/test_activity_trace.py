"""Tests for the bounded in-memory activity trace."""

import logging
from concurrent.futures import ThreadPoolExecutor

from core.activity_trace import ActivityTrace, activity_trace
from core.logger import get_logger, setup_logging


def test_trace_retains_exactly_the_newest_50_entries():
    """The oldest entry is evicted at the 51st append."""
    trace = ActivityTrace()
    for i in range(51):
        trace.add(f"entry {i}")
    _, entries = trace.snapshot()
    assert len(entries) == 50
    assert entries[0].endswith("entry 1")
    assert entries[-1].endswith("entry 50")


def test_trace_bounds_and_flattens_each_entry():
    """Large and multiline inputs cannot create unbounded retained strings."""
    trace = ActivityTrace()
    trace.add("first\n" + "x" * 10000)
    _, entries = trace.snapshot()
    assert len(entries[0]) == 512
    assert "\n" not in entries[0]


def test_trace_concurrent_writes_remain_bounded():
    """Concurrent producers never corrupt the snapshot or exceed the cap."""
    trace = ActivityTrace()
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda i: trace.add(f"entry {i}"), range(2000)))
    revision, entries = trace.snapshot()
    assert revision == 2000
    assert len(entries) == 50
    assert len(set(entries)) == 50


def test_visual_only_entries_are_not_written_to_file(app_root):
    """Visual-only trace messages do not create persistent log entries."""
    activity_trace.clear()
    log_file = setup_logging(app_root.logs_dir)
    get_logger("test").info("existing application log")
    activity_trace.add("visual-only event")
    for handler in logging.getLogger("ps_control").handlers:
        handler.flush()
    contents = log_file.read_text(encoding="utf-8")
    assert "existing application log" in contents
    assert "visual-only event" not in contents
    assert any("existing application log" in line for line in activity_trace.snapshot()[1])


def test_visual_info_remains_available_when_file_level_is_warning(app_root):
    """The visual trace does not change the configured file-log threshold."""
    activity_trace.clear()
    log_file = setup_logging(app_root.logs_dir, level="WARNING")
    get_logger("test").info("info visible only in trace")
    get_logger("test").warning("warning in both")
    for handler in logging.getLogger("ps_control").handlers:
        handler.flush()
    contents = log_file.read_text(encoding="utf-8")
    assert "info visible only in trace" not in contents
    assert "warning in both" in contents
    assert any("info visible only in trace" in line for line in activity_trace.snapshot()[1])
