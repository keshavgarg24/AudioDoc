"""JSON serialization of values returned from MongoDB."""
from __future__ import annotations

import datetime
import json

from labs.api.serialize import jsonable


def test_primitives_pass_through():
    assert jsonable(42) == 42
    assert jsonable("hello") == "hello"
    assert jsonable(True) is True
    assert jsonable(None) is None
    assert jsonable(3.14) == 3.14


def test_dict_is_recursed():
    d = {"a": 1, "b": {"c": 2}}
    assert jsonable(d) == {"a": 1, "b": {"c": 2}}


def test_list_is_recursed():
    assert jsonable([1, "two", 3.0]) == [1, "two", 3.0]


def test_tuple_becomes_list():
    assert jsonable((1, 2)) == [1, 2]


def test_datetime_becomes_isoformat_string():
    dt = datetime.datetime(2026, 1, 15, 9, 0, 0, tzinfo=datetime.timezone.utc)
    result = jsonable(dt)
    assert isinstance(result, str)
    assert "2026" in result


def test_date_becomes_isoformat_string():
    d = datetime.date(2026, 1, 15)
    result = jsonable(d)
    assert isinstance(result, str)
    assert "2026" in result


def test_bytes_become_none():
    assert jsonable(b"\x00\x01") is None


def test_bytearray_becomes_none():
    assert jsonable(bytearray(b"\xff")) is None


def test_report_gz_key_is_dropped():
    d = {"result": {"score": 1.0}, "report_gz": b"\xff\xfe"}
    out = jsonable(d)
    assert "report_gz" not in out
    assert "result" in out


def test_nested_report_gz_is_also_dropped():
    d = {"outer": {"report_gz": b"data", "value": 42}}
    out = jsonable(d)
    assert "report_gz" not in out["outer"]
    assert out["outer"]["value"] == 42


def test_nested_structure_is_json_serialisable():
    d = {
        "id": "abc123",
        "created_at": datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc),
        "result": {"score": 0.9, "items": [1, 2, 3]},
    }
    serialized = jsonable(d)
    json.dumps(serialized)


def test_objectid_like_class_becomes_string():
    class ObjectId:
        def __str__(self):
            return "507f1f77bcf86cd799439011"

    result = jsonable(ObjectId())
    assert result == "507f1f77bcf86cd799439011"
