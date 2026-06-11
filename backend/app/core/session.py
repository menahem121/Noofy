from __future__ import annotations

import uuid
from datetime import UTC, datetime

BACKEND_SESSION_ID = f"bs-{uuid.uuid4().hex}"
BACKEND_SESSION_STARTED_AT = datetime.now(UTC).isoformat()


def backend_session_payload() -> dict[str, str]:
    return {
        "backend_session_id": BACKEND_SESSION_ID,
        "backend_session_started_at": BACKEND_SESSION_STARTED_AT,
    }
