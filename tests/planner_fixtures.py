"""Deterministic planner fixtures shared by Task 5 tests and manual QA."""

from datetime import date, datetime
from typing import Final
from zoneinfo import ZoneInfo

from secretary_service.planner_models import (
    ActivityFlexibility,
    DayPlanRequest,
    EnergyWindow,
    PlanActivity,
)

EASTERN: Final = ZoneInfo("America/New_York")
PLAN_DATE: Final = date(2026, 9, 7)


def eastern(hour: int, minute: int = 0) -> datetime:
    """Build one minute-aligned local timestamp for the seeded plan date."""
    return datetime(2026, 9, 7, hour, minute, tzinfo=EASTERN)


def seeded_request() -> DayPlanRequest:
    """Build the work, workout, post, Farmers, laundry, and LifeOS fixture."""
    return DayPlanRequest(
        plan_date=PLAN_DATE,
        timezone="America/New_York",
        window_start=eastern(8),
        window_end=eastern(22),
        state_revision=7,
        expected_state_revision=7,
        energy_windows=(EnergyWindow(starts_at=eastern(18), ends_at=eastern(19), level=5),),
        activities=(
            PlanActivity(
                activity_id="work",
                title="Work",
                flexibility=ActivityFlexibility.FIXED,
                duration_minutes=480,
                fixed_start=eastern(9),
                fixed_end=eastern(17),
                display_group="work",
                display_title="Work",
            ),
            PlanActivity(
                activity_id="workout",
                title="Workout",
                flexibility=ActivityFlexibility.FLEXIBLE,
                duration_minutes=60,
                energy_required=5,
                goal_weight=4,
                importance=4,
                display_group="workout",
                display_title="Workout",
                guidance=("Strength session",),
            ),
            PlanActivity(
                activity_id="discussion-post",
                title="Discussion post",
                flexibility=ActivityFlexibility.FLEXIBLE,
                duration_minutes=45,
                deadline=datetime(2026, 9, 6, 23, 59, tzinfo=EASTERN),
                goal_weight=2,
                importance=5,
                display_group="coursework",
                display_title="Coursework deadline",
                guidance=("Submit the discussion post",),
            ),
            PlanActivity(
                activity_id="farmers",
                title="Farmers",
                flexibility=ActivityFlexibility.FIXED,
                duration_minutes=30,
                fixed_start=eastern(17, 15),
                fixed_end=eastern(17, 45),
                travel_minutes_before=15,
                travel_minutes_after=15,
                display_group="farmers-trip",
                display_title="Farmers errand",
                guidance=("Leave work at 5:00 PM", "Return home after pickup"),
            ),
            PlanActivity(
                activity_id="laundry",
                title="Laundry",
                flexibility=ActivityFlexibility.FLEXIBLE,
                duration_minutes=30,
                goal_weight=3,
                importance=4,
                display_group="evening-reset",
                display_title="Evening reset",
                guidance=("Start and fold laundry",),
            ),
            PlanActivity(
                activity_id="lifeos",
                title="LifeOS",
                flexibility=ActivityFlexibility.FLEXIBLE,
                duration_minutes=60,
                goal_weight=3,
                importance=3,
                display_group="evening-reset",
                display_title="Evening reset",
                guidance=("Finish the planner implementation",),
            ),
        ),
    )
