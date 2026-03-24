from src.fetcher import build_query, _strip_version


def test_strip_version_with_v():
    base, ver = _strip_version("2506.12345v2")
    assert base == "2506.12345"
    assert ver == 2


def test_strip_version_without_v():
    base, ver = _strip_version("2506.12345")
    assert base == "2506.12345"
    assert ver == 1


def test_build_query_basic():
    config = {
        "title_keywords": ["robot learning"],
        "abstract_keywords": ["sim-to-real"],
        "abstract_combos": [["robot", "policy learning"]],
        "categories": ["cs.RO"],
    }
    query = build_query(config)
    assert 'ti:"robot learning"' in query
    assert 'abs:"sim-to-real"' in query
    assert 'abs:"robot"' in query
    assert 'abs:"policy learning"' in query
    assert "cat:cs.RO" in query


def test_build_query_no_categories():
    config = {
        "title_keywords": ["world model"],
        "abstract_keywords": [],
        "abstract_combos": [],
    }
    query = build_query(config)
    assert 'ti:"world model"' in query
    assert "cat:" not in query


def test_build_query_multiple_combos():
    config = {
        "title_keywords": [],
        "abstract_keywords": [],
        "abstract_combos": [["a", "b"], ["c", "d"]],
        "categories": ["cs.AI"],
    }
    query = build_query(config)
    assert '(abs:"a" AND abs:"b")' in query
    assert '(abs:"c" AND abs:"d")' in query
