"""IntermediateStore — staging directory for handoff PNGs between pipeline invocations."""

import gc
import shutil
import time
from pathlib import Path


class IntermediateStore:
    """Manages a per-run directory for intermediate images and sub-manifests.

    Layout:
        <root>/
            <run_id>/
                01_<stage>.png
                01_<stage>.json   # sub-pipeline manifest
                02_<stage>.png
                ...

    The store is created lazily on first `path_for()` call.
    """

    def __init__(self, root: Path | str, run_id: str) -> None:
        self.root = Path(root) / run_id
        self.run_id = run_id
        self._stage_counter = 0
        self._created = False

    def _ensure(self) -> None:
        if not self._created:
            self.root.mkdir(parents=True, exist_ok=True)
            self._created = True

    def path_for(self, stage_name: str, ext: str = ".png") -> Path:
        """Return a unique path under the store for the given stage. Auto-numbers
        stages in the order they are requested so the directory listing is
        chronologically meaningful."""
        self._ensure()
        self._stage_counter += 1
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in stage_name)
        return self.root / f"{self._stage_counter:02d}_{safe}{ext}"

    def list(self) -> list[Path]:
        if not self._created:
            return []
        return sorted(self.root.iterdir())

    def cleanup(self, retries: int = 3, retry_delay_s: float = 0.2) -> bool:
        """Remove the entire run directory. No-op if it does not exist.

        Returns True if the directory is gone after the call, False if it
        could not be removed (e.g. another process holds a file open). On
        Windows, brief file locks from PIL's lazy file handles can linger
        after `with Image.open(...)` exits, so we GC + retry briefly.

        Note: subprocess-based per-pipeline runners create the directory
        themselves (via `output_dir.mkdir(...)`), bypassing `path_for()` /
        `_ensure()`, so `_created` is not a reliable existence check here.
        We always check the filesystem directly.
        """
        if not self.root.exists():
            self._created = False
            return True

        gc.collect()  # release any straggling lazy file handles before we try
        last_err: Exception | None = None
        for attempt in range(max(1, retries)):
            try:
                shutil.rmtree(self.root)
                self._created = False
                return True
            except OSError as e:
                last_err = e
                if attempt < retries - 1:
                    time.sleep(retry_delay_s)
        # Give up; surface a warning but don't raise -- cleanup is best-effort.
        print(f"[IntermediateStore] WARNING: could not remove {self.root}: {last_err}")
        return False

    def __str__(self) -> str:
        return str(self.root)
