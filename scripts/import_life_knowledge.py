#!/usr/bin/env python3
"""Import a private LifeOS bootstrap into the enrolled user's encrypted Life Model."""

import argparse
import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from secretary_service.keys import MacOSKeychainKeyProvider
from secretary_service.knowledge_import import import_knowledge
from secretary_service.life_interview import LifeInterview
from secretary_service.models import ActorId, CorrelationId, TransitionContext
from secretary_service.storage import EncryptedStateStore


class SystemClock:
    """Real import time, never substituted for a historical confirmation."""

    @staticmethod
    def now() -> datetime:
        """Return an aware instant."""
        return datetime.now(UTC)


def main() -> int:
    """Print only structural counts; errors never echo source values."""
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("--source", required=True, type=Path)
    _ = parser.add_argument("--state", required=True, type=Path)
    _ = parser.add_argument("--subject-id", required=True, type=UUID)
    _ = parser.add_argument("--source-id", default="chatgpt_bootstrap_v1")
    args = parser.parse_args()
    clock = SystemClock()
    context = TransitionContext(
        actor=ActorId("local-knowledge-import"),
        correlation_id=CorrelationId(str(uuid4())),
        occurred_at=clock.now(),
    )
    try:
        with EncryptedStateStore.open(args.state, MacOSKeychainKeyProvider(), clock) as store:
            if not any(
                i.record_id == args.subject_id for i in store.canonical_records().identities
            ):
                raise ValueError  # noqa: TRY301 - reject before entering the import transaction
            with store.memory.transaction():
                summary = import_knowledge(
                    args.source.read_bytes(),
                    store.memory,
                    str(args.subject_id),
                    args.source_id,
                    context,
                )
                _ = LifeInterview(store.memory, str(args.subject_id)).begin(
                    context, objective="week_planning"
                )
        print(json.dumps(asdict(summary), sort_keys=True))
    except Exception as error:  # noqa: BLE001 - redact all source-bearing parser/validation errors
        print(
            json.dumps(
                {"import": "failed; transaction rolled back", "error_type": type(error).__name__}
            )
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
