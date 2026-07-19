from pydantic import BaseModel

from review_fabric.serialization import canonical_json_bytes


class Evidence(BaseModel):
    path: str
    line: int


def test_canonical_json_bytes_sorts_mapping_keys() -> None:
    assert canonical_json_bytes({"z": 1, "a": {"second": 2, "first": 1}}) == (
        b'{"a":{"first":1,"second":2},"z":1}'
    )


def test_canonical_json_bytes_changes_when_evidence_changes() -> None:
    baseline = {"evidence": [{"path": "src/api.py", "line": 42}]}
    changed = {"evidence": [{"path": "src/api.py", "line": 43}]}

    assert canonical_json_bytes(baseline) != canonical_json_bytes(changed)


def test_canonical_json_bytes_serializes_pydantic_models() -> None:
    assert canonical_json_bytes(Evidence(path="src/api.py", line=42)) == (
        b'{"line":42,"path":"src/api.py"}'
    )
