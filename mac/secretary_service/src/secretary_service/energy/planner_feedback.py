"""Constrained energy-pattern feedback for deterministic plan proposals."""

from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import Final, override
from zoneinfo import ZoneInfo

from pydantic import Field

from secretary_service.energy.models import EnergyModel, EnergyPattern, TimeBucket
from secretary_service.planner import propose_day
from secretary_service.planner_models import (
    ActivityFlexibility,
    DayPlanRequest,
    EnergyWindow,
    ExistingPlanBlock,
)
from secretary_service.planner_results import PlanBlockKind, ProposedDay

MINIMUM_PLANNER_CONFIDENCE: Final = 0.6


class PlannerEnergyFeedback(EnergyModel):
    """Auditable explanation of whether and how a pattern affected a proposal."""

    applied: bool
    bucket: TimeBucket
    confidence: float = Field(ge=0.0, le=1.0)
    provenance_ids: tuple[str, ...]
    affected_activity_ids: tuple[str, ...] = ()
    basis: str = "self_reported_energy"


class EnergyReplanResult(EnergyModel):
    """A proposed day paired with confidence and provenance facts."""

    proposal: ProposedDay
    feedback: PlannerEnergyFeedback


@dataclass(frozen=True, slots=True)
class UnsafeEnergyReplanError(Exception):
    """Planner feedback attempted to alter a non-flexible block."""

    activity_ids: tuple[str, ...]

    @override
    def __str__(self) -> str:
        return f"energy feedback changed ineligible activities: {', '.join(self.activity_ids)}"


def _bucket_intervals(
    pattern: EnergyPattern,
    request: DayPlanRequest,
) -> tuple[tuple[datetime, datetime], ...]:
    zone = ZoneInfo(request.timezone)
    day = request.plan_date
    bounds: tuple[tuple[datetime, datetime], ...] = ()
    match pattern.bucket:  # noqa: MATCH_OK - basedpyright proves enum exhaustiveness.
        case TimeBucket.MORNING:
            bounds = (
                (datetime.combine(day, time(5), zone), datetime.combine(day, time(12), zone)),
            )
        case TimeBucket.AFTERNOON:
            bounds = (
                (datetime.combine(day, time(12), zone), datetime.combine(day, time(17), zone)),
            )
        case TimeBucket.EVENING:
            bounds = (
                (datetime.combine(day, time(17), zone), datetime.combine(day, time(22), zone)),
            )
        case TimeBucket.NIGHT:
            bounds = (
                (datetime.combine(day, time.min, zone), datetime.combine(day, time(5), zone)),
                (
                    datetime.combine(day, time(22), zone),
                    datetime.combine(day + timedelta(days=1), time.min, zone),
                ),
            )
    window_start = request.window_start.astimezone(UTC)
    window_end = request.window_end.astimezone(UTC)
    clipped: list[tuple[datetime, datetime]] = []
    for starts_at, ends_at in bounds:
        start = max(starts_at.astimezone(UTC), window_start)
        end = min(ends_at.astimezone(UTC), window_end)
        if start < end:
            clipped.append((start.astimezone(zone), end.astimezone(zone)))
    return tuple(clipped)


def _baseline(proposal: ProposedDay) -> tuple[ExistingPlanBlock, ...]:
    return tuple(
        ExistingPlanBlock(
            block_id=block.block_id,
            activity_id=block.activity_id,
            title=block.title,
            starts_at=block.starts_at,
            ends_at=block.ends_at,
        )
        for block in proposal.internal_plan
        if block.activity_id is not None
        and block.kind in (PlanBlockKind.FIXED, PlanBlockKind.FLEXIBLE, PlanBlockKind.PROTECTED)
    )


def _protected_intervals(proposal: ProposedDay) -> tuple[tuple[str, datetime, datetime], ...]:
    return tuple(
        (block.activity_id or "", block.starts_at, block.ends_at)
        for block in proposal.internal_plan
        if block.kind in (PlanBlockKind.FIXED, PlanBlockKind.PROTECTED)
    )


def apply_energy_pattern(
    request: DayPlanRequest,
    previous: ProposedDay,
    pattern: EnergyPattern,
) -> EnergyReplanResult:
    """Apply a supported soft preference to flexible proposal blocks only."""
    base_feedback = PlannerEnergyFeedback(
        applied=False,
        bucket=pattern.bucket,
        confidence=pattern.confidence,
        provenance_ids=pattern.provenance_ids,
    )
    intervals = _bucket_intervals(pattern, request)
    if pattern.confidence < MINIMUM_PLANNER_CONFIDENCE or not intervals:
        return EnergyReplanResult(proposal=previous, feedback=base_feedback)
    energy_windows = tuple(
        EnergyWindow(starts_at=start, ends_at=end, level=pattern.expected_level)
        for start, end in intervals
    )
    replanned = propose_day(
        request.model_copy(
            update={
                "baseline_blocks": _baseline(previous),
                "energy_windows": energy_windows,
            }
        )
    )
    if _protected_intervals(previous) != _protected_intervals(replanned):
        protected_ids = tuple(item[0] for item in _protected_intervals(previous))
        raise UnsafeEnergyReplanError(protected_ids)
    eligible = {
        activity.activity_id
        for activity in request.activities
        if activity.flexibility is ActivityFlexibility.FLEXIBLE
    }
    changed = {
        item.activity_id
        for item in (*replanned.diff.added, *replanned.diff.removed, *replanned.diff.moved)
    }
    ineligible = tuple(sorted(changed - eligible))
    if ineligible:
        raise UnsafeEnergyReplanError(ineligible)
    affected = tuple(sorted(changed))
    return EnergyReplanResult(
        proposal=replanned,
        feedback=base_feedback.model_copy(
            update={"applied": bool(affected), "affected_activity_ids": affected}
        ),
    )
