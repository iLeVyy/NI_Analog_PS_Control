"""Nonmodal, bounded live view of the current application activity."""

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QDialog, QLabel, QPlainTextEdit, QVBoxLayout, QWidget

from core.activity_trace import ActivityTrace, activity_trace


class LogWindow(QDialog):
    """Display the most recent in-memory trace entries."""

    def __init__(self, parent: QWidget, trace: ActivityTrace = activity_trace) -> None:
        """Create a resizable, read-only log window."""
        super().__init__(parent)
        self.setWindowTitle("Activity Log")
        self.resize(760, 420)
        self._trace = trace
        self._revision = -1
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Current session only - newest 50 entries", self))
        self.entries = QPlainTextEdit(self)
        self.entries.setReadOnly(True)
        self.entries.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        layout.addWidget(self.entries)
        self._timer = QTimer(self)
        self._timer.setInterval(200)
        self._timer.timeout.connect(self.refresh)
        self._timer.start()
        self.refresh()

    def refresh(self) -> None:
        """Refresh from a bounded snapshot only when the trace changes."""
        revision, entries = self._trace.snapshot()
        if revision != self._revision:
            self._revision = revision
            self.entries.setPlainText("\n".join(entries))
            scrollbar = self.entries.verticalScrollBar()
            scrollbar.setValue(scrollbar.maximum())
