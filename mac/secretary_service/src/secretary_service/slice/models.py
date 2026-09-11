"""Typed boundaries for the tomorrow planning vertical slice."""

from datetime import datetime
from pathlib import Path
from typing import ClassVar
from uuid import UUID

from pydantic import ConfigDict, Field

from secretary_service.authority import Approval, ProposalRecord, Rollback
from secretary_service.conversation_api import (
    Conversation,
    ConversationDevice,
    ConversationEvent,
)
from secretary_service.google_calendar_contract import (
    CalendarApplyResult,
    CalendarPreview,
    CalendarRollbackResult,
)
from secretary_service.models import FrozenModel, NonEmpty, RecordId
from secretary_service.planner_models import DayPlanRequest
from secretary_service.planner_results import ProposedDay


class SliceModel(FrozenModel):
    """Strict immutable model used at slice and fixture boundaries."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")


class FixedClock(SliceModel):
    """Fixture clock satisfying the service clock protocol."""

    current: datetime

    def now(self) -> datetime:
        """Return the fixture's fixed aware timestamp."""
        return self.current


class ConversationCapture(SliceModel):
    """Ordered transcript events forming one natural-language capture."""

    events: tuple[ConversationEvent, ...] = Field(min_length=2)


class CaptureReceipt(SliceModel):
    """Accepted transcript capture and canonical source identity."""

    accepted_event_ids: tuple[UUID, ...]
    next_sequence: int = Field(ge=1)
    source_record_id: RecordId
    text: NonEmpty
    source_fingerprint: NonEmpty


class InterpretationSeed(SliceModel):
    """Schema-validated fixture output from the interpretation boundary."""

    text: NonEmpty
    proposal_id: RecordId
    provider_version: NonEmpty
    plan_request: DayPlanRequest


class InterpretationProposal(SliceModel):
    """Interpretation proposal linked to its canonical transcript source."""

    proposal_id: RecordId
    source_event_id: UUID
    provider_version: NonEmpty
    plan_request: DayPlanRequest


class TomorrowPreview(SliceModel):
    """Auditable user-visible reasoning, diff, and Calendar dry run."""

    capture: CaptureReceipt
    interpretation: InterpretationProposal
    authorization: ProposalRecord
    plan: ProposedDay
    calendar: CalendarPreview


class TomorrowApplyResult(SliceModel):
    """Applied proposal state and owned Calendar operation evidence."""

    authorization: ProposalRecord
    calendar: CalendarApplyResult


class ReplanCommand(SliceModel):
    """Changed-schedule capture linked to the previously applied proposal."""

    previous_proposal_id: RecordId
    capture: ConversationCapture


class TomorrowReplanResult(SliceModel):
    """Changed-shift preview with explicit protected-constraint evidence."""

    previous_proposal_id: RecordId
    preview: TomorrowPreview
    preserved_protected_activity_ids: tuple[NonEmpty, ...]


class TomorrowRollbackResult(SliceModel):
    """Reverted proposal state and owned-event deletion evidence."""

    authorization: ProposalRecord
    calendar: CalendarRollbackResult


class SandboxFixture(SliceModel):
    """Hermetic Google Calendar sandbox configuration."""

    owner_id: NonEmpty
    secret_reference: NonEmpty
    access_token: NonEmpty
    primary_event_id: NonEmpty

    @classmethod
    def from_path(cls, path: Path) -> "SandboxFixture":
        """Parse one checked-in sandbox boundary fixture."""
        return cls.model_validate_json(path.read_text(encoding="utf-8"))


class TomorrowFixtureSeed(SliceModel):
    """Inputs required to drive the deterministic E2E scenario."""

    clock: FixedClock
    conversation: Conversation
    devices: tuple[ConversationDevice, ...]
    interpretations: tuple[InterpretationSeed, ...]
    initial_capture: ConversationCapture
    replan: ReplanCommand
    approval: Approval
    rollback: Rollback


class TomorrowGolden(SliceModel):
    """Exact observable outputs for every E2E phase."""

    preview: TomorrowPreview
    apply: TomorrowApplyResult
    replan: TomorrowReplanResult
    cleanup: TomorrowRollbackResult


class TomorrowFixture(TomorrowFixtureSeed):
    """Complete E2E fixture including exact golden responses."""

    golden: TomorrowGolden
