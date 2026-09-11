"""Consented energy check-ins and confidence-qualified replanning tests."""

from datetime import UTC, date, datetime, time, timedelta

import pytest
from pydantic import ValidationError

from secretary_service.energy import (
    CheckInScheduleRequest,
    CheckInStatus,
    CompletedCheckIn,
    DailyWindow,
    EnergyConsent,
    EnergyObservation,
    EnergyPreferences,
    EnergyResponse,
    MissedCheckIn,
    ScheduledCheckIn,
    SnoozedCheckIn,
    SnoozeRequest,
    SuppressedNotification,
    SuppressionReason,
    TimeBucket,
    apply_energy_pattern,
    learn_energy_patterns,
    mark_missed,
    respond_to_check_in,
    schedule_check_in,
    snooze_check_in,
)
from secretary_service.planner import propose_day
from secretary_service.planner_models import ActivityFlexibility, DayPlanRequest, PlanActivity
from secretary_service.planner_results import PlanBlockKind


def preferences(*, quiet: DailyWindow | None = None) -> EnergyPreferences:
    return EnergyPreferences(
        timezone="UTC",
        awake_window=DailyWindow(starts_at=time(7), ends_at=time(23)),
        quiet_window=quiet,
        response_window_minutes=45,
        snooze_minutes=15,
    )


def consent(now: datetime, *, granted: bool = True) -> EnergyConsent:
    return EnergyConsent(
        granted=granted,
        decided_at=now - timedelta(days=1),
        provenance_id="settings-energy-consent-v1",
    )


def observation(check_in_id: str, hour: int, rating: int) -> EnergyObservation:
    return EnergyObservation(
        check_in_id=check_in_id,
        rating=rating,
        observed_at=datetime(2026, 9, 1, hour, tzinfo=UTC),
        provenance_id=f"notification:{check_in_id}",
    )


def planning_request() -> DayPlanRequest:
    def at(hour: int) -> datetime:
        return datetime(2026, 9, 6, hour, tzinfo=UTC)

    return DayPlanRequest(
        plan_date=date(2026, 9, 6),
        timezone="UTC",
        window_start=at(12),
        window_end=at(18),
        state_revision=9,
        expected_state_revision=9,
        activities=(
            PlanActivity(
                activity_id="a-low",
                title="Low energy task",
                flexibility=ActivityFlexibility.FLEXIBLE,
                duration_minutes=60,
                energy_required=1,
            ),
            PlanActivity(
                activity_id="z-high",
                title="High energy task",
                flexibility=ActivityFlexibility.FLEXIBLE,
                duration_minutes=60,
                energy_required=5,
            ),
            PlanActivity(
                activity_id="protected",
                title="Protected commitment",
                flexibility=ActivityFlexibility.PROTECTED,
                duration_minutes=60,
                fixed_start=at(14),
                fixed_end=at(15),
            ),
            PlanActivity(
                activity_id="fixed",
                title="Fixed commitment",
                flexibility=ActivityFlexibility.FIXED,
                duration_minutes=60,
                fixed_start=at(16),
                fixed_end=at(17),
            ),
        ),
    )


def test_response_boundary_accepts_only_actionable_one_to_five_values() -> None:
    # Given/When/Then: notification responses outside the five declared actions fail closed.
    responded_at = datetime(2026, 9, 5, 14, tzinfo=UTC)
    assert [
        EnergyResponse(rating=value, responded_at=responded_at).rating for value in range(1, 6)
    ] == [1, 2, 3, 4, 5]
    with pytest.raises(ValidationError):
        _ = EnergyResponse(rating=0, responded_at=responded_at)
    with pytest.raises(ValidationError):
        _ = EnergyResponse(rating=6, responded_at=responded_at)


def test_quiet_hours_suppress_a_check_in_inside_the_awake_window(fake_now: datetime) -> None:
    # Given: consent and awake hours overlap a configured quiet window.
    config = preferences(quiet=DailyWindow(starts_at=time(13), ends_at=time(15)))

    # When: the scheduler evaluates the fake 14:00 clock.
    request = CheckInScheduleRequest(check_in_id="check-in-quiet", evaluated_at=fake_now)
    decision = schedule_check_in(config, consent(fake_now), request)

    # Then: no notification is scheduled during quiet hours.
    assert isinstance(decision, SuppressedNotification)
    assert decision.reason is SuppressionReason.QUIET_HOURS


def test_awake_window_suppresses_overnight_notifications(fake_now: datetime) -> None:
    # Given: an overnight evaluation outside the configured awake window.
    evaluated_at = fake_now.replace(hour=2)
    request = CheckInScheduleRequest(check_in_id="check-in-asleep", evaluated_at=evaluated_at)

    # When: the scheduler applies the user-controlled awake window.
    decision = schedule_check_in(preferences(), consent(evaluated_at), request)

    # Then: no proactive notification is emitted while the user is asleep.
    assert isinstance(decision, SuppressedNotification)
    assert decision.reason is SuppressionReason.OUTSIDE_AWAKE_WINDOW


def test_withdrawn_consent_blocks_scheduling_and_response(fake_now: datetime) -> None:
    # Given: consent was explicitly withdrawn before a prompt and before a response.
    config = preferences()
    request = CheckInScheduleRequest(check_in_id="check-in-consent", evaluated_at=fake_now)
    active = schedule_check_in(config, consent(fake_now), request)
    assert isinstance(active, ScheduledCheckIn)
    withdrawn = consent(fake_now, granted=False)

    # When/Then: both future scheduling and an action on the old prompt fail closed.
    new_request = CheckInScheduleRequest(check_in_id="new-check-in", evaluated_at=fake_now)
    suppressed = schedule_check_in(config, withdrawn, new_request)
    assert isinstance(suppressed, SuppressedNotification)
    assert suppressed.reason is SuppressionReason.CONSENT_REQUIRED
    response = respond_to_check_in(
        active,
        EnergyResponse(rating=4, responded_at=fake_now),
        withdrawn,
    )
    assert isinstance(response, SuppressedNotification)
    assert response.reason is SuppressionReason.CONSENT_REQUIRED


def test_snooze_and_missed_transitions_are_deterministic(fake_now: datetime) -> None:
    # Given: an eligible prompt with a fixed 15-minute snooze policy.
    config = preferences()
    active_consent = consent(fake_now)
    request = CheckInScheduleRequest(check_in_id="check-in-snooze", evaluated_at=fake_now)
    prompt = schedule_check_in(config, active_consent, request)
    assert isinstance(prompt, ScheduledCheckIn)

    # When: the action is snoozed and later evaluated after its response deadline.
    snoozed = snooze_check_in(
        prompt,
        SnoozeRequest(
            preferences=config,
            consent=active_consent,
            acted_at=fake_now,
        ),
    )
    assert isinstance(snoozed, SnoozedCheckIn)
    missed = mark_missed(snoozed, fake_now + timedelta(minutes=61))
    assert isinstance(missed, MissedCheckIn)

    # Then: timestamps and terminal state derive only from the injected clock and policy.
    assert snoozed.status is CheckInStatus.SNOOZED
    assert snoozed.scheduled_at == fake_now + timedelta(minutes=15)
    assert snoozed.respond_by == fake_now + timedelta(minutes=60)
    assert missed.status is CheckInStatus.MISSED


def test_scheduled_response_records_self_report_provenance(fake_now: datetime) -> None:
    # Given: a consented scheduled notification.
    active_consent = consent(fake_now)
    request = CheckInScheduleRequest(check_in_id="check-in-response", evaluated_at=fake_now)
    prompt = schedule_check_in(preferences(), active_consent, request)
    assert isinstance(prompt, ScheduledCheckIn)

    # When: the user taps one actionable energy value.
    completed = respond_to_check_in(
        prompt,
        EnergyResponse(rating=5, responded_at=fake_now + timedelta(minutes=2)),
        active_consent,
    )
    assert isinstance(completed, CompletedCheckIn)

    # Then: the local observation is attributed without mood, diagnosis, or clinical fields.
    assert completed.status is CheckInStatus.RESPONDED
    assert completed.observation.rating == 5
    assert completed.observation.provenance_id == "notification:check-in-response"
    assert set(completed.observation.model_dump()) == {
        "check_in_id",
        "rating",
        "observed_at",
        "provenance_id",
    }


def test_sparse_data_produces_no_pattern_or_planner_feedback(fake_now: datetime) -> None:
    # Given: fewer than three consented self-reports.
    samples = (observation("one", 14, 5), observation("two", 14, 4))

    # When: local pattern learning runs.
    patterns = learn_energy_patterns(samples, "UTC", fake_now)

    # Then: sparse history fails closed instead of inventing a preference.
    assert patterns == ()


def test_pattern_is_confidence_qualified_and_provenance_complete(fake_now: datetime) -> None:
    # Given: three consistent afternoon self-reports.
    samples = tuple(observation(identifier, 14, 5) for identifier in ("one", "two", "three"))

    # When: the local non-clinical learner aggregates them.
    pattern = learn_energy_patterns(samples, "UTC", fake_now)[0]

    # Then: it records uncertainty and every source without retaining extra health context.
    assert pattern.bucket is TimeBucket.AFTERNOON
    assert pattern.expected_level == 5
    assert pattern.sample_count == 3
    assert pattern.confidence == 0.6
    assert pattern.provenance_ids == ("notification:one", "notification:three", "notification:two")
    assert pattern.basis == "self_reported_energy"


def test_feedback_replans_only_flexible_blocks_and_preserves_protected_fixed(
    fake_now: datetime,
) -> None:
    # Given: history plus a current response to a scheduled afternoon check-in.
    request = planning_request()
    original = propose_day(request)
    active_consent = consent(fake_now)
    prompt = schedule_check_in(
        preferences(),
        active_consent,
        CheckInScheduleRequest(check_in_id="current", evaluated_at=fake_now),
    )
    assert isinstance(prompt, ScheduledCheckIn)
    completed = respond_to_check_in(
        prompt,
        EnergyResponse(rating=5, responded_at=fake_now),
        active_consent,
    )
    assert isinstance(completed, CompletedCheckIn)
    samples = (
        observation("one", 14, 5),
        observation("two", 14, 5),
        completed.observation,
    )
    pattern = learn_energy_patterns(samples, "UTC", fake_now)[0]

    # When: confidence-qualified feedback is applied to a new proposal only.
    result = apply_energy_pattern(request, original, pattern)

    # Then: flexible proposals move, while protected/fixed blocks remain byte-for-byte equal.
    assert result.feedback.applied is True
    assert result.feedback.confidence == 0.6
    assert result.feedback.provenance_ids == pattern.provenance_ids
    assert result.feedback.affected_activity_ids == ("a-low", "z-high")
    assert {move.activity_id for move in result.proposal.diff.moved} == {"a-low", "z-high"}
    for kind in (PlanBlockKind.PROTECTED, PlanBlockKind.FIXED):
        before = tuple(block for block in original.internal_plan if block.kind is kind)
        after = tuple(block for block in result.proposal.internal_plan if block.kind is kind)
        assert after == before
