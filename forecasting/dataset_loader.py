"""Schema-tolerant loader for ai-prophet-datasets releases + ad-hoc files.

Supports:
- `events.json` (list of events OR wrapper object)
- `tasks.jsonl` (one event per line)
- A directory containing `release.json` + `tasks.jsonl` (ai-prophet-datasets
  release folder)
- A directory containing one or more `*.jsonl` files (sharded layout)

Returns an iterable of `ForecastTask` after normalization. The loader
does NOT silently swallow malformed rows; it raises by default, and a
permissive mode collects errors into a `LoadResult.errors` list.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

from .normalize import normalize_one, unwrap_payload
from .schema import ForecastTask, SchemaError


@dataclass
class LoadResult:
    tasks: List[ForecastTask] = field(default_factory=list)
    errors: List[Tuple[str, str]] = field(default_factory=list)  # (source_id, message)
    release_info: dict = field(default_factory=dict)
    source_path: Optional[Path] = None

    def summary(self) -> dict:
        binary = sum(1 for t in self.tasks if t.is_binary)
        resolved = sum(1 for t in self.tasks if t.is_resolved)
        return {
            "total": len(self.tasks),
            "binary": binary,
            "multi_outcome": len(self.tasks) - binary,
            "resolved": resolved,
            "unresolved": len(self.tasks) - resolved,
            "errors": len(self.errors),
            "source_path": str(self.source_path) if self.source_path else None,
            "release_info": self.release_info,
        }


# -- Path-shape detection -------------------------------------------------


def _is_release_dir(path: Path) -> bool:
    return path.is_dir() and (path / "release.json").exists() and (path / "tasks.jsonl").exists()


def _is_jsonl_dir(path: Path) -> bool:
    return path.is_dir() and any(p.suffix == ".jsonl" for p in path.iterdir())


# -- Atom loaders ---------------------------------------------------------


def _iter_jsonl(path: Path) -> Iterable[Tuple[int, dict]]:
    with path.open() as f:
        for i, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                yield i, json.loads(line)
            except json.JSONDecodeError as exc:
                raise SchemaError(
                    f"{path}:line {i} not valid JSON: {exc}",
                    raw={"line": line[:200]},
                )


def _load_jsonl(path: Path, strict: bool = True) -> LoadResult:
    result = LoadResult(source_path=path)
    for line_no, raw in _iter_jsonl(path):
        try:
            task = normalize_one(raw)
            result.tasks.append(task)
        except SchemaError as exc:
            entry = (f"{path.name}:{line_no}", str(exc))
            result.errors.append(entry)
            if strict:
                raise
    return result


def _load_json(path: Path, strict: bool = True) -> LoadResult:
    payload = json.loads(path.read_text())
    raws = unwrap_payload(payload)
    result = LoadResult(source_path=path)
    for i, raw in enumerate(raws):
        try:
            task = normalize_one(raw)
            result.tasks.append(task)
        except SchemaError as exc:
            entry = (f"{path.name}[{i}]", str(exc))
            result.errors.append(entry)
            if strict:
                raise
    return result


def _load_release_dir(path: Path, strict: bool = True) -> LoadResult:
    release_json = path / "release.json"
    tasks_jsonl = path / "tasks.jsonl"
    release_info = {}
    if release_json.exists():
        try:
            release_info = json.loads(release_json.read_text())
        except json.JSONDecodeError:
            pass
    sub = _load_jsonl(tasks_jsonl, strict=strict)
    sub.release_info = release_info
    sub.source_path = path
    return sub


def _load_jsonl_dir(path: Path, strict: bool = True) -> LoadResult:
    result = LoadResult(source_path=path)
    for child in sorted(path.glob("*.jsonl")):
        sub = _load_jsonl(child, strict=strict)
        result.tasks.extend(sub.tasks)
        result.errors.extend(sub.errors)
    return result


# -- Public entry point ---------------------------------------------------


def load(path: Path, strict: bool = True) -> LoadResult:
    """Load whatever Rob (or organizers) gave us. Auto-detects shape.

    Args:
        path: a file or directory.
        strict: when True (default), the first SchemaError aborts the
            load. When False, the loader collects errors into
            `result.errors` and continues. Use strict=False for
            inspection passes; use strict=True for production.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(path)

    if path.is_file():
        if path.suffix == ".jsonl":
            return _load_jsonl(path, strict=strict)
        if path.suffix in (".json",):
            return _load_json(path, strict=strict)
        raise ValueError(f"unrecognized file extension: {path.suffix}")

    # Directories
    if _is_release_dir(path):
        return _load_release_dir(path, strict=strict)
    if _is_jsonl_dir(path):
        return _load_jsonl_dir(path, strict=strict)
    raise ValueError(f"directory {path} contains no recognizable dataset layout")


def load_many(paths: Iterable[Path], strict: bool = True) -> LoadResult:
    """Concatenate multiple sources into one LoadResult."""
    combined = LoadResult()
    for p in paths:
        sub = load(p, strict=strict)
        combined.tasks.extend(sub.tasks)
        combined.errors.extend(sub.errors)
        if sub.release_info and not combined.release_info:
            combined.release_info = sub.release_info
    return combined
