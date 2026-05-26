"""Filesystem watcher that debounce-triggers full reindex on change.

This is the "soft" incremental path: scip-typescript can't index a
single file, so we still run the whole pipeline — but we don't make
the developer remember to hit `/reindex` after every edit.

Run via the CLI module:

    uv run python -m kg.watcher <project>

Watches `<project.code_root>/src/` and re-runs `reindex(project)`
after `WATCH_DEBOUNCE_S` seconds of quiescence. SCIP indexes are
re-built from scratch; extractors run fully. Memgraph wipes only the
target project's slice — other projects in the same graph are left
alone.
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

from watchdog.events import FileSystemEvent, PatternMatchingEventHandler
from watchdog.observers import Observer

from .indexers.ingest import reindex
from .settings import ProjectConfig, settings

WATCH_DEBOUNCE_S = 1.5
WATCH_PATTERNS = ["*.ts", "*.tsx", "*.scss", "*.module.scss", "*.md", "tsconfig.json"]
WATCH_IGNORES = ["*/node_modules/*", "*/dist/*", "*/.git/*", "*/.run/*", "*/.logs/*"]


class _DebouncedReindex(PatternMatchingEventHandler):
    def __init__(self, project: ProjectConfig) -> None:
        super().__init__(
            patterns=WATCH_PATTERNS,
            ignore_patterns=WATCH_IGNORES,
            ignore_directories=True,
        )
        self.project = project
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()
        self._pending_paths: set[str] = set()

    def on_any_event(self, event: FileSystemEvent) -> None:  # noqa: D401
        if event.is_directory:
            return
        with self._lock:
            self._pending_paths.add(event.src_path)
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(WATCH_DEBOUNCE_S, self._reindex)
            self._timer.daemon = True
            self._timer.start()

    def _reindex(self) -> None:
        with self._lock:
            paths = sorted(self._pending_paths)
            self._pending_paths.clear()
        print(f"[watcher] {len(paths)} change(s); reindex {self.project.name}…", flush=True)
        try:
            t0 = time.monotonic()
            result = reindex(self.project)
            ms = int((time.monotonic() - t0) * 1000)
            ok = result.get("ok")
            print(f"[watcher] reindex done in {ms}ms (ok={ok})", flush=True)
        except Exception as e:
            print(f"[watcher] reindex FAILED: {type(e).__name__}: {e}", flush=True)


def watch(project: ProjectConfig) -> None:
    src = project.code_root / "src"
    if not src.is_dir():
        print(f"[watcher] no src/ under {project.code_root}", file=sys.stderr)
        sys.exit(2)

    handler = _DebouncedReindex(project)
    observer = Observer()
    observer.schedule(handler, str(src), recursive=True)
    observer.start()
    print(f"[watcher] watching {src} (debounce {WATCH_DEBOUNCE_S}s)", flush=True)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()


def main() -> None:
    if len(sys.argv) != 2:
        print("usage: python -m kg.watcher <project>", file=sys.stderr)
        sys.exit(2)
    name = sys.argv[1]
    project = settings.project(name)
    if project is None:
        print(f"project '{name}' not configured — set PROJECT_{name.upper()} in .env", file=sys.stderr)
        sys.exit(2)
    watch(project)


if __name__ == "__main__":
    main()
