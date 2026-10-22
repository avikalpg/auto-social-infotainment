from __future__ import annotations

from pathlib import Path

import pytest

from workflow_automation.editorial import (
    append_feedback,
    load_feedback,
    summarize_feedback_for_prompt,
)
from workflow_automation.llm_client import LLMClient


def test_corrupted_feedback_json_recovers_gracefully(tmp_path: Path) -> None:
    bad_file = tmp_path / "bad.json"
    bad_file.write_text("{invalid json")
    assert load_feedback(bad_file) == []


def test_append_feedback_contract(tmp_path: Path) -> None:
    feedback_file = tmp_path / "feedback.json"
    entry = append_feedback(
        feedback_file,
        text="Punchy hook needed",
        story_id="STR-005",
        target="hook",
        weight="high",
    )
    assert entry["text"] == "Punchy hook needed"
    assert entry["story_id"] == "STR-005"
    assert entry["target"] == "hook"
    assert entry["weight"] == "high"
    assert "timestamp" in entry

    items = load_feedback(feedback_file)
    assert len(items) == 1
    assert items[0]["text"] == "Punchy hook needed"

    summary = summarize_feedback_for_prompt(items)
    assert "[HOOK]" in summary
    assert "Story: STR-005" in summary


def test_empty_candidates_raises_value_error() -> None:
    client = LLMClient(allow_fallback=True)
    with pytest.raises(ValueError, match="No candidate stories"):
        client.select_and_rank_stories([])


def test_llm_client_production_fail_closed_without_cmd() -> None:
    client = LLMClient(command=None, allow_fallback=False)
    with pytest.raises(RuntimeError, match="LLM adapter command is required for production story selection"):
        client.select_and_rank_stories([{"id": "STR-001", "story": "Test"}])

    with pytest.raises(RuntimeError, match="LLM adapter command is required for production factual review"):
        client.review_factual_claims({"id": "STR-001"}, {})

    with pytest.raises(RuntimeError, match="LLM adapter command is required for production platform copy generation"):
        client.generate_platform_copy("STR-001", "Script", {})


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
