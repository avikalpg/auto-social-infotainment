from __future__ import annotations
from pathlib import Path
from typing import Any
import json
from .contracts import validate_notebook_request, validate_notebook_receipt
from .packages import atomic_json
from .state import utcnow


def write_worker_request(path: Path, story: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    req = {
        'schema_version': 1,
        'request_id': f"notebook-{story['id']}",
        'story_id': str(story['id']),
        'source_url': str(story['source_url']),
        'output_dir': str(output_dir),
        'timestamp': utcnow(),
    }
    validate_notebook_request(req); atomic_json(path, req); return req


def ingest_worker_receipt(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text())
    validate_notebook_receipt(data)
    return dict(data['artifacts'])
