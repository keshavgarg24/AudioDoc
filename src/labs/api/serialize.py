"""JSON-safe coercion for values that came out of MongoDB.

Mongo hands back `datetime` and `ObjectId`, and a compressed report field is
raw `bytes`. FastAPI's JSONResponse uses a plain `json.dumps`, which raises
`TypeError` on all three - and that raise happens *after* the work is done, so
the user waits for a long analysis and then gets a 500 with the result already
computed and thrown away.

Every endpoint that returns a document assembled from stored data must pass it
through here.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any


def jsonable(obj: Any) -> Any:
    """Recursively convert stored values into JSON-serialisable ones.

    `report_gz` is dropped rather than encoded: it is the compressed blob, it
    is never useful to a client, and base64-ing it would inflate a response by
    tens of kilobytes.
    """
    if isinstance(obj, dict):
        return {k: jsonable(v) for k, v in obj.items() if k != "report_gz"}
    if isinstance(obj, (list, tuple)):
        return [jsonable(v) for v in obj]
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, (bytes, bytearray)):
        return None
    if obj.__class__.__name__ == "ObjectId":
        return str(obj)
    return obj
