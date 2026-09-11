"""Internal minute blocks and human-readable planner explanations."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from secretary_service.planner_models import (
    ActivityFlexibility,
    DayPlanRequest,
    PlanActivity,
)
from secretary_service.planner_placement import Placement
from secretary_service.planner_results import (
    ExplanationCode,
    PlanBlock,
    PlanBlockKind,
    TaskExplanation,
    UnscheduledActivity,
)
from secretary_service.planner_scoring import score_activity


def build_internal_plan(
    request: DayPlanRequest,
    placements: tuple[Placement, ...],
) -> tuple[PlanBlock, ...]:
    """Render placements and free time into gapless minute-level blocks."""
    zone = ZoneInfo(request.timezone)
    activity_blocks: list[PlanBlock] = []
    for placement in placements:
        activity = placement.activity
        code = _explanation_code(activity, request)
        if activity.travel_minutes_before:
            activity_blocks.append(_travel_block(placement, before=True, zone=zone, code=code))
        activity_blocks.append(
            PlanBlock(
                block_id=f"activity:{activity.activity_id}",
                activity_id=activity.activity_id,
                title=activity.title,
                kind=_block_kind(activity.flexibility),
                starts_at=placement.starts_at.astimezone(zone),
                ends_at=placement.ends_at.astimezone(zone),
                display_group=activity.display_group,
                display_title=activity.display_title,
                guidance=activity.guidance,
                deadline=activity.deadline,
                explanation_code=code,
                calendar_eligible=activity.calendar_eligible,
                personal_micro_event=activity.personal_micro_event,
            )
        )
        if activity.travel_minutes_after:
            activity_blocks.append(_travel_block(placement, before=False, zone=zone, code=code))
    activity_blocks.sort(key=lambda block: (block.starts_at.astimezone(UTC), block.block_id))
    result: list[PlanBlock] = []
    cursor = request.window_start.astimezone(UTC)
    for block in activity_blocks:
        block_start = block.starts_at.astimezone(UTC)
        if cursor < block_start:
            result.append(_available_block(cursor, block_start, zone))
        result.append(block)
        cursor = block.ends_at.astimezone(UTC)
    window_end = request.window_end.astimezone(UTC)
    if cursor < window_end:
        result.append(_available_block(cursor, window_end, zone))
    return tuple(result)


def explain_activity(
    activity: PlanActivity,
    request: DayPlanRequest,
    placements: list[Placement],
    unscheduled: list[UnscheduledActivity],
    dependent_count: int,
) -> TaskExplanation:
    """Explain one scheduled or unscheduled activity with exact score fields."""
    placement = next(
        (item for item in placements if item.activity.activity_id == activity.activity_id),
        None,
    )
    if placement is None:
        reason = next(item for item in unscheduled if item.activity_id == activity.activity_id)
        return TaskExplanation(
            activity_id=activity.activity_id,
            code=ExplanationCode.INFEASIBLE,
            summary=reason.detail,
            score=score_activity(activity, request, request.window_start, dependent_count),
            scheduled_block_ids=(),
            constraints=_constraints(activity),
        )
    code = _explanation_code(activity, request)
    match code:  # noqa: MATCH_OK - all ExplanationCode variants are explicit.
        case ExplanationCode.OVERDUE:
            overdue_minutes = (
                int(
                    (
                        request.window_start.astimezone(UTC) - activity.deadline.astimezone(UTC)
                    ).total_seconds()
                    // 60
                )
                if activity.deadline is not None
                else 0
            )
            hours, minutes = divmod(overdue_minutes, 60)
            hour_label = "hour" if hours == 1 else "hours"
            minute_label = "minute" if minutes == 1 else "minutes"
            summary = (
                f"Scheduled first because its deadline was overdue by {hours} {hour_label} "
                f"and {minutes} {minute_label} at the planning window."
            )
        case ExplanationCode.FIXED:
            summary = "Kept at its fixed time and protected from flexible work."
        case ExplanationCode.PROTECTED:
            summary = "Preserved at its protected time before placing flexible work."
        case ExplanationCode.SCHEDULED:
            summary = "Placed in the highest-scoring available minute interval."
        case ExplanationCode.INFEASIBLE:
            summary = "The activity could not be placed without violating constraints."
    ids = [f"activity:{activity.activity_id}"]
    if activity.travel_minutes_before:
        ids.insert(0, f"travel-before:{activity.activity_id}")
    if activity.travel_minutes_after:
        ids.append(f"travel-after:{activity.activity_id}")
    return TaskExplanation(
        activity_id=activity.activity_id,
        code=code,
        summary=summary,
        score=placement.score,
        scheduled_block_ids=tuple(ids),
        constraints=_constraints(activity),
    )


def infeasible_explanation(
    activity: PlanActivity,
    request: DayPlanRequest,
    dependent_count: int,
) -> TaskExplanation:
    """Explain a hard fixed/protected conflict without suggesting output."""
    return TaskExplanation(
        activity_id=activity.activity_id,
        code=ExplanationCode.INFEASIBLE,
        summary="Fixed or protected reservation conflicts with another required interval.",
        score=score_activity(
            activity,
            request,
            activity.fixed_start or request.window_start,
            dependent_count,
        ),
        scheduled_block_ids=(),
        constraints=_constraints(activity),
    )


def _travel_block(
    placement: Placement,
    *,
    before: bool,
    zone: ZoneInfo,
    code: ExplanationCode,
) -> PlanBlock:
    activity = placement.activity
    if before:
        starts_at = placement.reservation_start
        ends_at = placement.starts_at
        direction = "to"
    else:
        starts_at = placement.ends_at
        ends_at = placement.reservation_end
        direction = "from"
    return PlanBlock(
        block_id=f"travel-{'before' if before else 'after'}:{activity.activity_id}",
        activity_id=activity.activity_id,
        title=f"Travel {direction} {activity.title}",
        kind=PlanBlockKind.TRAVEL,
        starts_at=starts_at.astimezone(zone),
        ends_at=ends_at.astimezone(zone),
        display_group=activity.display_group,
        display_title=activity.display_title or activity.title,
        guidance=activity.guidance,
        deadline=activity.deadline,
        explanation_code=code,
        calendar_eligible=activity.calendar_eligible,
        personal_micro_event=activity.personal_micro_event,
    )


def _available_block(starts_at: datetime, ends_at: datetime, zone: ZoneInfo) -> PlanBlock:
    return PlanBlock(
        block_id=f"available:{starts_at.isoformat()}",
        activity_id=None,
        title="Available",
        kind=PlanBlockKind.AVAILABLE,
        starts_at=starts_at.astimezone(zone),
        ends_at=ends_at.astimezone(zone),
    )


def _block_kind(flexibility: ActivityFlexibility) -> PlanBlockKind:
    match flexibility:  # noqa: MATCH_OK - basedpyright proves enum exhaustiveness.
        case ActivityFlexibility.FIXED:
            return PlanBlockKind.FIXED
        case ActivityFlexibility.FLEXIBLE:
            return PlanBlockKind.FLEXIBLE
        case ActivityFlexibility.PROTECTED:
            return PlanBlockKind.PROTECTED


def _explanation_code(activity: PlanActivity, request: DayPlanRequest) -> ExplanationCode:
    if activity.deadline is not None and activity.deadline.astimezone(
        UTC
    ) < request.window_start.astimezone(UTC):
        return ExplanationCode.OVERDUE
    match activity.flexibility:  # noqa: MATCH_OK - basedpyright proves enum exhaustiveness.
        case ActivityFlexibility.FIXED:
            return ExplanationCode.FIXED
        case ActivityFlexibility.PROTECTED:
            return ExplanationCode.PROTECTED
        case ActivityFlexibility.FLEXIBLE:
            return ExplanationCode.SCHEDULED


def _constraints(activity: PlanActivity) -> tuple[str, ...]:
    values: list[str] = [f"duration={activity.duration_minutes}m"]
    if activity.deadline is not None:
        values.append(f"deadline={activity.deadline.isoformat()}")
    if activity.dependencies:
        values.append(f"dependencies={','.join(activity.dependencies)}")
    if activity.travel_minutes_before or activity.travel_minutes_after:
        values.append(f"travel={activity.travel_minutes_before}m/{activity.travel_minutes_after}m")
    values.extend((f"energy={activity.energy_required}", f"goal_weight={activity.goal_weight}"))
    return tuple(values)
