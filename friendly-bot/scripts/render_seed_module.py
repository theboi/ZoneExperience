"""Render one named object exported by a local Python seed module as JSON."""

from __future__ import annotations

import json
import runpy
import sys
from pathlib import Path
from typing import cast


def main() -> None:
    """Render the requested named seed object without printing module internals."""

    if len(sys.argv) != 3:
        raise SystemExit("expected a Python seed path and exported object name")
    path = Path(sys.argv[1])
    export_name = sys.argv[2]
    try:
        namespace = runpy.run_path(str(path))
    except Exception as exc:
        raise SystemExit("could not load Python seed module") from exc
    value = namespace.get(export_name)
    if not isinstance(value, dict):
        raise SystemExit("Python seed module must export an object")
    try:
        print(json.dumps(cast(dict[str, object], value), ensure_ascii=False))
    except (TypeError, ValueError) as exc:
        raise SystemExit("Python seed module must export JSON-safe data") from exc


if __name__ == "__main__":
    main()
