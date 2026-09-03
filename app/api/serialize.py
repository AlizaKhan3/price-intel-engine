from __future__ import annotations

from datetime import datetime

from bson import ObjectId


def dump_doc(doc: dict | None) -> dict | None:
    if doc is None:
        return None
    out = {}
    for key, value in doc.items():
        if key == "_id":
            out["id"] = str(value)
            continue
        if key == "api_key_hash":
            continue
        out[key] = _jsonable(value)
    return out


def dump_docs(docs: list[dict]) -> list[dict]:
    return [dump_doc(d) for d in docs]


def _jsonable(value):
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat() + "Z"
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items() if k != "api_key_hash"}
    return value
