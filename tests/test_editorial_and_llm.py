from __future__ import annotations

from pathlib import Path

import pytest

from workflow_automation.editorial import (
    load_feedback,
)
from workflow_automation.llm_client import LLMClient


def test_corrupted_feedback_json_recovers_gracefully(tmp_path: Path) -> None:
    bad_file = tmp_path / "bad.json"
    bad_file.write_text("{invalid json")
    assert load_feedback(bad_file) == []


def test_empty_candidates_raises_value_error() -> None:
    client = LLMClient()
    with pytest.raises(ValueError, match="No candidate stories"):
        client.select_and_rank_stories([])


def test_llm_client_external_command_mock(tmp_path: Path) -> None:
    mock_llm_script = tmp_path / "mock_llm.py"
    mock_llm_script.write_text(
        "import sys, json\n"
        "payload = json.loads(sys.stdin.read())\n"
        "if payload.get('task') == 'story_selection':\n"
        "    print(json.dumps({\n"
        "        'selected_story_id': 'STR-999',\n"
        "        'rationale': 'Mock LLM top pick',\n"
        "        'confidence_score': 0.88,\n"
        "        'ranked_story_ids': ['STR-999']\n"
        "    }))\n"
    )

    client = LLMClient(command=("python3", str(mock_llm_script)))
    res = client.select_and_rank_stories([{"id": "STR-999", "story": "Test"}])
    assert res.selected_story_id == "STR-999"
    assert res.confidence_score == 0.88
    assert res.rationale == "Mock LLM top pick"
