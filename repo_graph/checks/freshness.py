from __future__ import annotations

from pathlib import Path
from typing import Mapping


def compare_generated_outputs(outputs: Mapping[Path, str]) -> list[Path]:
    stale_paths = []
    for path, expected_text in outputs.items():
        if not path.exists():
            stale_paths.append(path)
            continue
        if path.read_text(encoding="utf-8") != expected_text:
            stale_paths.append(path)
    return sorted(stale_paths)
