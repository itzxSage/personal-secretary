"""Human-readable Calendar projection and exact plan diff construction."""

from secretary_service.planner_models import DayPlanRequest
from secretary_service.planner_results import (
    BlockChange,
    CalendarBlock,
    MovedBlock,
    PlanBlock,
    PlanBlockKind,
    PlanDiff,
)


def project_calendar(blocks: tuple[PlanBlock, ...]) -> tuple[CalendarBlock, ...]:
    """Consolidate adjacent eligible blocks without exposing micro-events."""
    projected: list[CalendarBlock] = []
    for block in blocks:
        if (
            block.kind is PlanBlockKind.AVAILABLE
            or not block.calendar_eligible
            or block.personal_micro_event
            or block.activity_id is None
        ):
            continue
        group = block.display_group or block.activity_id
        title = block.display_title or block.title
        if projected and _can_merge(projected[-1], block, group, title):
            previous = projected[-1]
            activity_ids = _append_unique(previous.source_activity_ids, block.activity_id)
            deadlines = previous.deadlines
            if block.deadline is not None and block.deadline not in deadlines:
                deadlines = (*deadlines, block.deadline)
            codes = previous.explanation_codes
            if block.explanation_code is not None and block.explanation_code not in codes:
                codes = (*codes, block.explanation_code)
            projected[-1] = previous.model_copy(
                update={
                    "block_id": f"calendar:{activity_ids[0]}:{activity_ids[-1]}",
                    "ends_at": block.ends_at,
                    "source_activity_ids": activity_ids,
                    "segment_titles": _append_unique(previous.segment_titles, block.title),
                    "transitions": (
                        *previous.transitions,
                        f"{previous.segment_titles[-1]} -> {block.title}",
                    ),
                    "deadlines": deadlines,
                    "guidance": _append_many_unique(previous.guidance, block.guidance),
                    "explanation_codes": codes,
                }
            )
            continue
        projected.append(
            CalendarBlock(
                block_id=f"calendar:{block.activity_id}:{block.activity_id}",
                display_group=group,
                title=title,
                starts_at=block.starts_at,
                ends_at=block.ends_at,
                source_activity_ids=(block.activity_id,),
                segment_titles=(block.title,),
                transitions=(),
                deadlines=() if block.deadline is None else (block.deadline,),
                guidance=block.guidance,
                explanation_codes=(
                    () if block.explanation_code is None else (block.explanation_code,)
                ),
            )
        )
    return tuple(projected)


def build_diff(request: DayPlanRequest, blocks: tuple[PlanBlock, ...]) -> PlanDiff:
    """Compare proposed activity blocks with the caller's exact baseline."""
    proposed = {
        block.block_id: block
        for block in blocks
        if block.activity_id is not None and block.kind is not PlanBlockKind.AVAILABLE
    }
    baseline = {block.block_id: block for block in request.baseline_blocks}
    added = tuple(
        _as_change(proposed[block_id]) for block_id in sorted(proposed.keys() - baseline.keys())
    )
    removed = tuple(
        BlockChange(
            block_id=baseline[block_id].block_id,
            activity_id=baseline[block_id].activity_id,
            title=baseline[block_id].title,
            starts_at=baseline[block_id].starts_at,
            ends_at=baseline[block_id].ends_at,
        )
        for block_id in sorted(baseline.keys() - proposed.keys())
    )
    moved = tuple(
        MovedBlock(
            block_id=block_id,
            activity_id=proposed[block_id].activity_id or "unreachable",
            title=proposed[block_id].title,
            previous_starts_at=baseline[block_id].starts_at,
            previous_ends_at=baseline[block_id].ends_at,
            proposed_starts_at=proposed[block_id].starts_at,
            proposed_ends_at=proposed[block_id].ends_at,
        )
        for block_id in sorted(proposed.keys() & baseline.keys())
        if proposed[block_id].starts_at != baseline[block_id].starts_at
        or proposed[block_id].ends_at != baseline[block_id].ends_at
    )
    return PlanDiff(added=added, removed=removed, moved=moved)


def _can_merge(
    previous: CalendarBlock,
    block: PlanBlock,
    group: str,
    title: str,
) -> bool:
    return (
        previous.ends_at == block.starts_at
        and previous.title == title
        and previous.display_group == group
    )


def _append_unique(values: tuple[str, ...], value: str) -> tuple[str, ...]:
    return values if value in values else (*values, value)


def _append_many_unique(values: tuple[str, ...], additions: tuple[str, ...]) -> tuple[str, ...]:
    result = values
    for addition in additions:
        result = _append_unique(result, addition)
    return result


def _as_change(block: PlanBlock) -> BlockChange:
    return BlockChange(
        block_id=block.block_id,
        activity_id=block.activity_id or "unreachable",
        title=block.title,
        starts_at=block.starts_at,
        ends_at=block.ends_at,
    )
