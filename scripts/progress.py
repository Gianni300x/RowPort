"""Interactive, single-line progress for CSV imports."""


class ProgressBar:
    def __init__(self, stream):
        self.stream = stream
        self.width = 0
        self.last_percent = None
        self.total = None
        self.active = False

    def _draw(self, message):
        self.stream.write("\r" + message + " " * max(0, self.width - len(message)))
        self.stream.flush()
        self.width = len(message)
        self.active = True

    def counting(self):
        self._draw("Counting CSV records...")

    def start(self, total):
        self.total = total
        self.update(0, 0, 0, 0)

    def update(self, processed, saved, rejected, skipped):
        percent = min(100, processed * 100 // self.total) if self.total else 100
        if percent == self.last_percent:
            return
        self.last_percent = percent
        filled = percent // 5
        self._draw(f"[{'#' * filled}{'-' * (20 - filled)}] {percent:3}%  {processed}/{self.total} processed")

    def finish(self):
        if self.active:
            self.stream.write("\n")
            self.stream.flush()
            self.active = False
