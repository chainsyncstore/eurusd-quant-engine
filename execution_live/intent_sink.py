from __future__ import annotations

import uuid
from pathlib import Path


class FileIntentSink:
    def __init__(self, root_dir: str):
        self.root = Path(root_dir)
        self.pending = self.root / "pending"
        self.done = self.root / "done"
        self.failed = self.root / "failed"
        for path in (self.pending, self.done, self.failed):
            path.mkdir(parents=True, exist_ok=True)

    def emit(self, intent_json: str) -> None:
        fname = f"{uuid.uuid4()}.json"
        tmp = self.pending / f"{fname}.tmp"
        final = self.pending / fname
        tmp.write_text(intent_json)
        tmp.rename(final)
