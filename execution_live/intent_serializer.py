from __future__ import annotations

import json
from dataclasses import asdict


def serialize_intent(intent) -> str:
    data = asdict(intent)
    timestamp = data.get("timestamp")
    if timestamp is not None:
        data["timestamp"] = timestamp.isoformat()
    return json.dumps(data, separators=(",", ":"))
