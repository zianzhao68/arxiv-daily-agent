import json

from src.relevance_filter import _parse_response


def test_parse_valid_json():
    raw = json.dumps([
        {"id": "2506.00001", "verdict": "core", "direction": "embodied_ai", "confidence": 0.9, "reason": "VLA model"},
        {"id": "2506.00002", "verdict": "not_relevant", "direction": "none", "confidence": 0.8, "reason": "unrelated"},
    ])
    results = _parse_response(raw, ["2506.00001", "2506.00002"])
    assert len(results) == 2
    assert results[0].verdict == "core"
    assert results[0].direction == "embodied_ai"
    assert results[1].verdict == "not_relevant"


def test_parse_backwards_compat_relevant_maps_to_core():
    raw = json.dumps([
        {"id": "2506.00001", "verdict": "relevant", "direction": "world_models", "confidence": 0.7},
    ])
    results = _parse_response(raw, ["2506.00001"])
    assert len(results) == 1
    assert results[0].verdict == "core"


def test_parse_with_markdown_fences():
    raw = '```json\n[{"id": "2506.00001", "verdict": "core", "direction": "world_models", "confidence": 0.7, "reason": "world model"}]\n```'
    results = _parse_response(raw, ["2506.00001"])
    assert len(results) == 1
    assert results[0].verdict == "core"


def test_parse_failure_defaults_to_peripheral():
    raw = "this is not json"
    results = _parse_response(raw, ["2506.00001", "2506.00002"])
    assert len(results) == 2
    assert all(r.verdict == "peripheral" for r in results)
    assert all(r.error == "parse_failure" for r in results)


def test_parse_missing_paper_defaults_to_peripheral():
    raw = json.dumps([
        {"id": "2506.00001", "verdict": "not_relevant", "direction": "none", "confidence": 0.9, "reason": "off-topic"},
    ])
    results = _parse_response(raw, ["2506.00001", "2506.00002"])
    assert len(results) == 2
    assert results[0].verdict == "not_relevant"
    assert results[1].verdict == "peripheral"
    assert results[1].error == "missing_from_response"


def test_parse_invalid_verdict_defaults_to_peripheral():
    raw = json.dumps([
        {"id": "2506.00001", "verdict": "maybe", "direction": "embodied_ai", "confidence": 0.5},
    ])
    results = _parse_response(raw, ["2506.00001"])
    assert results[0].verdict == "peripheral"


def test_parse_peripheral_verdict():
    raw = json.dumps([
        {"id": "2506.00001", "verdict": "peripheral", "direction": "embodied_ai", "confidence": 0.6, "reason": "RL but no foundation model"},
    ])
    results = _parse_response(raw, ["2506.00001"])
    assert results[0].verdict == "peripheral"
    assert results[0].reason == "RL but no foundation model"
