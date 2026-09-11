"""Validated inputs for deterministic proposed-day planning."""

from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Annotated, ClassVar, Literal, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import ConfigDict, Field, StringConstraints, model_validator

from secretary_service.models import FrozenModel

NonEmptyText = Annotated[str, StringConstraints(min_length=1)]


class PlannerModel(FrozenModel):
    """Immutable planner boundary model that rejects unknown fields."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")


class ActivityFlexibility(StrEnum):
    """Placement contract for an activity."""

    FIXED = "fixed"
    FLEXIBLE = "flexible"
    PROTECTED = "protected"


class ScheduleResolution(PlannerModel):
    """Explicit internal and external schedule resolution contract."""

    internal_plan: Literal["minute-level"] = "minute-level"
    calendar_projection: Literal["human-readable-blocks"] = "human-readable-blocks"


def _validate_minute(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        message = f"{field_name} must be timezone-aware"
        raise ValueError(message)
    if value.second != 0 or value.microsecond != 0:
        message = f"{field_name} must use minute resolution"
        raise ValueError(message)


class EnergyWindow(PlannerModel):
    """User-supplied energy availability over a local time interval."""

    starts_at: datetime
    ends_at: datetime
    level: int = Field(ge=1, le=5)

    @model_validator(mode="after")
    def validate_interval(self) -> Self:
        """Reject naive, sub-minute, or reversed energy windows."""
        _validate_minute(self.starts_at, "starts_at")
        _validate_minute(self.ends_at, "ends_at")
        if self.ends_at.astimezone(UTC) <= self.starts_at.astimezone(UTC):
            message = "energy window must end after it starts"
            raise ValueError(message)
        return self


class PlanActivity(PlannerModel):
    """Task or event constraints consumed by the deterministic planner."""

    activity_id: NonEmptyText
    title: NonEmptyText
    flexibility: ActivityFlexibility
    duration_minutes: int = Field(ge=1, le=1440)
    fixed_start: datetime | None = None
    fixed_end: datetime | None = None
    earliest_start: datetime | None = None
    latest_end: datetime | None = None
    deadline: datetime | None = None
    recover_missed_deadline: bool = False
    dependencies: tuple[NonEmptyText, ...] = ()
    travel_minutes_before: int = Field(default=0, ge=0, le=1440)
    travel_minutes_after: int = Field(default=0, ge=0, le=1440)
    energy_required: int = Field(default=1, ge=1, le=5)
    goal_weight: int = Field(default=0, ge=0, le=10)
    importance: int = Field(default=0, ge=0, le=10)
    display_group: NonEmptyText | None = None
    display_title: NonEmptyText | None = None
    guidance: tuple[NonEmptyText, ...] = ()
    calendar_eligible: bool = True
    personal_micro_event: bool = False

    @model_validator(mode="after")
    def validate_constraints(self) -> Self:
        """Reject contradictory activity placement constraints."""
        for field_name, value in (
            ("fixed_start", self.fixed_start),
            ("fixed_end", self.fixed_end),
            ("earliest_start", self.earliest_start),
            ("latest_end", self.latest_end),
            ("deadline", self.deadline),
        ):
            if value is not None:
                _validate_minute(value, field_name)
        _validate_activity_placement(self)
        if self.recover_missed_deadline and self.deadline is None:
            message = "deadline recovery requires an explicit deadline"
            raise ValueError(message)
        if (
            self.latest_end is not None
            and self.earliest_start is not None
            and self.latest_end.astimezone(UTC) <= self.earliest_start.astimezone(UTC)
        ):
            message = "latest_end must follow earliest_start"
            raise ValueError(message)
        if self.activity_id in self.dependencies:
            message = "an activity cannot depend on itself"
            raise ValueError(message)
        return self


class ExistingPlanBlock(PlannerModel):
    """Previously proposed block used to compute an exact replan diff."""

    block_id: NonEmptyText
    activity_id: NonEmptyText
    title: NonEmptyText
    starts_at: datetime
    ends_at: datetime

    @model_validator(mode="after")
    def validate_interval(self) -> Self:
        """Reject malformed baseline intervals before computing a diff."""
        _validate_minute(self.starts_at, "starts_at")
        _validate_minute(self.ends_at, "ends_at")
        if self.ends_at.astimezone(UTC) <= self.starts_at.astimezone(UTC):
            message = "baseline block must end after it starts"
            raise ValueError(message)
        return self


class DayPlanRequest(PlannerModel):
    """Complete canonical-state snapshot required to propose one day."""

    plan_date: date
    timezone: NonEmptyText
    window_start: datetime
    window_end: datetime
    state_revision: int = Field(ge=1)
    expected_state_revision: int = Field(ge=1)
    activities: tuple[PlanActivity, ...]
    energy_windows: tuple[EnergyWindow, ...] = ()
    baseline_blocks: tuple[ExistingPlanBlock, ...] = ()
    schedule_resolution: ScheduleResolution = ScheduleResolution()

    @model_validator(mode="after")
    def validate_snapshot(self) -> Self:
        """Reject stale shapes, duplicate IDs, and invalid local timestamps."""
        try:
            zone = ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError as error:
            message = f"unknown timezone: {self.timezone}"
            raise ValueError(message) from error
        timestamps = [self.window_start, self.window_end]
        timestamps.extend(window.starts_at for window in self.energy_windows)
        timestamps.extend(window.ends_at for window in self.energy_windows)
        for activity in self.activities:
            timestamps.extend(
                value
                for value in (
                    activity.fixed_start,
                    activity.fixed_end,
                    activity.earliest_start,
                    activity.latest_end,
                    activity.deadline,
                )
                if value is not None
            )
        for value in timestamps:
            _validate_minute(value, "planner timestamp")
            local = value.astimezone(UTC).astimezone(zone)
            if local.replace(tzinfo=None) != value.replace(tzinfo=None):
                message = f"nonexistent local time: {value.isoformat()}"
                raise ValueError(message)
            if local.utcoffset() != value.utcoffset():
                message = f"timestamp offset does not match {self.timezone}: {value.isoformat()}"
                raise ValueError(message)
        if self.window_end.astimezone(UTC) <= self.window_start.astimezone(UTC):
            message = "planning window must end after it starts"
            raise ValueError(message)
        if self.window_start.astimezone(zone).date() != self.plan_date:
            message = "window_start must fall on plan_date"
            raise ValueError(message)
        activity_ids = [activity.activity_id for activity in self.activities]
        if len(activity_ids) != len(set(activity_ids)):
            message = "duplicate activity_id"
            raise ValueError(message)
        baseline_ids = [block.block_id for block in self.baseline_blocks]
        if len(baseline_ids) != len(set(baseline_ids)):
            message = "duplicate baseline block_id"
            raise ValueError(message)
        return self


def _validate_activity_placement(activity: PlanActivity) -> None:
    match activity.flexibility:  # noqa: MATCH_OK - basedpyright proves enum exhaustiveness.
        case ActivityFlexibility.FIXED | ActivityFlexibility.PROTECTED:
            if activity.fixed_start is None or activity.fixed_end is None:
                message = "fixed and protected activities require fixed_start and fixed_end"
                raise ValueError(message)
            actual_minutes = int(
                (
                    activity.fixed_end.astimezone(UTC) - activity.fixed_start.astimezone(UTC)
                ).total_seconds()
                // 60
            )
            if actual_minutes != activity.duration_minutes:
                message = "duration_minutes must match the fixed interval"
                raise ValueError(message)
        case ActivityFlexibility.FLEXIBLE:
            if activity.fixed_start is not None or activity.fixed_end is not None:
                message = "flexible activities cannot define a fixed interval"
                raise ValueError(message)
