"""Content-minimized audit entries for governed learning."""

import hmac
from typing import final

from secretary_service.audit import AuditMutation
from secretary_service.keys import KeyProvider
from secretary_service.ledger import AuditLedger
from secretary_service.models import RecordId, RecordKind, TransitionContext


@final
class LearningAuditor:
    """Create content-minimized learning audit entries."""

    def __init__(self, keys: KeyProvider, ledger: AuditLedger) -> None:
        """Bind audit keys and the chained ledger."""
        self._keys = keys
        self._ledger = ledger

    def fingerprint(self, payload: str) -> str:
        """Return the keyed fingerprint allowed to survive content erasure."""
        return hmac.digest(self._keys.audit_key(), payload.encode(), "sha256").hex()

    def verify(self) -> None:
        """Block learning mutation when the audit chain is untrusted."""
        _ = self._ledger.verify()

    def append(
        self,
        record_id: RecordId,
        action: str,
        fingerprint: str,
        context: TransitionContext,
    ) -> None:
        """Append an audit entry without embedding learning content."""
        self._ledger.append(
            AuditMutation(
                record_kind=RecordKind.NORMALIZED_FACT,
                record_id=record_id,
                state=action,
                action_class=f"learning.{action}",
                source_fingerprint=fingerprint,
                context=context,
            )
        )
