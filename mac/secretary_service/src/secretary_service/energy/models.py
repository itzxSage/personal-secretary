"""Typed boundaries for private energy check-ins and local patterns."""

from datetime import datetime, time
from enum import StrEnum
from typing import Annotated, ClassVar, Literal, Self

from pydantic import ConfigDict, Field, StringConstraints, field_validator, model_validator

from secretary_service.models import FrozenModel

NonEmptyText = Annotated[str, StringConstraints(min_length=1)]


class EnergyModel(FrozenModel):
    """Immutable energy boundary that rejects unknown fields."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")


class DailyWindow(EnergyModel):
    """Local wall-clock window, including windows that cross midnight."""

    starts_at: time
    ends_at: time

    @model_validator(mode="after")
    def validate_window(self) -> Self:
        """Reject an ambiguous zero-length daily window."""
        if self.starts_at == self.ends_at:
            message = "daily window start and end must differ"
            raise ValueError(message)
        return self


class EnergyPreferences(EnergyModel):
    """User-controlled notification timing and deterministic action policy."""

    timezone: NonEmptyText
    awake_window: DailyWindow
    quiet_window: DailyWindow | None = None
    notifications_enabled: bool = True
    response_window_minutes: int = Field(default=45, ge=1, le=180)
    snooze_minutes: int = Field(default=15, ge=1, le=180)


class EnergyConsent(EnergyModel):
    """Explicit local consent decision for energy check-ins."""

    granted: bool
    decided_at: datetime
    provenance_id: NonEmptyText

    @field_validator("decided_at")
    @classmethod
    def validate_decided_at(cls, value: datetime) -> datetime:
        """Require an aware consent timestamp."""
        if value.tzinfo is None or value.utcoffset() is None:
            message = "decided_at must be timezone-aware"
            raise ValueError(message)
        return value


class CheckInScheduleRequest(EnergyModel):
    """One deterministic scheduler evaluation."""

    check_in_id: NonEmptyText
    evaluated_at: datetime


class SnoozeRequest(EnergyModel):
    """Policy and authority facts bound to a snooze action."""

    preferences: EnergyPreferences
    consent: EnergyConsent
    acted_at: datetime


class EnergyResponse(EnergyModel):
    """Actionable notification response constrained to the five offered values."""

    rating: int = Field(ge=1, le=5)
    responded_at: datetime


class CheckInStatus(StrEnum):
    """Closed check-in lifecycle states."""

    SCHEDULED = "scheduled"
    SNOOZED = "snoozed"
    RESPONDED = "responded"
    MISSED = "missed"


class SuppressionReason(StrEnum):
    """Fail-closed reasons that prevent a proactive notification or action."""

    CONSENT_REQUIRED = "consent_required"
    NOTIFICATIONS_DISABLED = "notifications_disabled"
    OUTSIDE_AWAKE_WINDOW = "outside_awake_window"
    QUIET_HOURS = "quiet_hours"


class ScheduledCheckIn(EnergyModel):
    """Notification eligible for response or snooze."""

    check_in_id: NonEmptyText
    status: Literal[CheckInStatus.SCHEDULED] = CheckInStatus.SCHEDULED
    scheduled_at: datetime
    respond_by: datetime
    snooze_count: int = 0


class SnoozedCheckIn(EnergyModel):
    """Notification deferred by an explicit user action."""

    check_in_id: NonEmptyText
    status: Literal[CheckInStatus.SNOOZED] = CheckInStatus.SNOOZED
    scheduled_at: datetime
    respond_by: datetime
    snooze_count: int = Field(ge=1)


class MissedCheckIn(EnergyModel):
    """Terminal prompt that expired without inventing a response."""

    check_in_id: NonEmptyText
    status: Literal[CheckInStatus.MISSED] = CheckInStatus.MISSED
    missed_at: datetime
    snooze_count: int = Field(ge=0)


class SuppressedNotification(EnergyModel):
    """Content-free evidence that no notification or response was accepted."""

    check_in_id: NonEmptyText
    reason: SuppressionReason


class EnergyObservation(EnergyModel):
    """Minimal local self-report retained for non-clinical pattern learning."""

    check_in_id: NonEmptyText
    rating: int = Field(ge=1, le=5)
    observed_at: datetime
    provenance_id: NonEmptyText


class CompletedCheckIn(EnergyModel):
    """Terminal check-in containing only the actionable self-report."""

    check_in_id: NonEmptyText
    status: Literal[CheckInStatus.RESPONDED] = CheckInStatus.RESPONDED
    observation: EnergyObservation
    snooze_count: int = Field(ge=0)


class TimeBucket(StrEnum):
    """Non-clinical local time-of-day grouping."""

    MORNING = "morning"
    AFTERNOON = "afternoon"
    EVENING = "evening"
    NIGHT = "night"


class EnergyPattern(EnergyModel):
    """Confidence-qualified aggregate derived only from self-reported energy."""

    bucket: TimeBucket
    expected_level: int = Field(ge=1, le=5)
    sample_count: int = Field(ge=3)
    confidence: float = Field(ge=0.0, le=1.0)
    provenance_ids: tuple[NonEmptyText, ...] = Field(min_length=3)
    learned_at: datetime
    basis: Literal["self_reported_energy"] = "self_reported_energy"


type ActiveCheckIn = ScheduledCheckIn | SnoozedCheckIn
type NotificationDecision = ActiveCheckIn | SuppressedNotification
type CheckInActionResult = CompletedCheckIn | MissedCheckIn | SuppressedNotification
