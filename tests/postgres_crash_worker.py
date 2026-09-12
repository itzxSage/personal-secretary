"""Child process used only to test real disconnect/crash transaction behavior."""

import os
from uuid import UUID

from pydantic import SecretBytes

from secretary_service.cloud_crypto import CloudStateKeys
from secretary_service.postgres_store import PostgresStateStore
from tests.helpers import FakeClock
from tests.test_domain_transactions import source
from tests.test_encrypted_state_audit import context


def crash() -> None:
    keys = CloudStateKeys(
        SecretBytes(bytes.fromhex(os.environ["LIFEOS_CRASH_DATA_KEY"])),
        SecretBytes(bytes.fromhex(os.environ["LIFEOS_CRASH_AUDIT_KEY"])),
    )
    clock = FakeClock.from_isoformat(os.environ["LIFEOS_CRASH_NOW"])
    with PostgresStateStore.connect(
        os.environ["LIFEOS_CRASH_DSN"], UUID(os.environ["LIFEOS_CRASH_TENANT"]), keys
    ) as store:
        with store.execution_transaction() as unit:
            if os.environ["LIFEOS_CRASH_MODE"] == "uncommitted":
                unit.records.create(source(clock), context(clock, "child", "crash"))
                assert unit.consumption.consume((("crash", "uncommitted"),))
                os._exit(23)
            assert unit.outbox.claim(context(clock, "child", "claim")) is not None
        os._exit(23)


if __name__ == "__main__":
    crash()
