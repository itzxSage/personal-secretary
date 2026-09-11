"""Deterministic consent and timing policy for energy check-ins."""

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from secretary_service.energy.models import (
    ActiveCheckIn,
    CheckInActionResult,
    CheckInScheduleRequest,
    CompletedCheckIn,
    DailyWindow,
    EnergyConsent,
    EnergyObservation,
    EnergyPreferences,
    EnergyResponse,
    MissedCheckIn,
    NotificationDecision,
    ScheduledCheckIn,
    SnoozedCheckIn,
    SnoozeRequest,
    SuppressedNotification,
    SuppressionReason,
)


def _contains(window: DailyWindow, value: time) -> bool:
    if window.starts_at < window.ends_at:
        return window.starts_at <= value < window.ends_at
    return value >= window.starts_at or value < window.ends_at


def _suppressed(check_in_id: str, reason: SuppressionReason) -> SuppressedNotification:
    return SuppressedNotification(check_in_id=check_in_id, reason=reason)


def _suppression_reason(
    preferences: EnergyPreferences,
    consent: EnergyConsent,
    evaluated_at: datetime,
) -> SuppressionReason | None:
    if not consent.granted or consent.decided_at > evaluated_at:
        return SuppressionReason.CONSENT_REQUIRED
    if not preferences.notifications_enabled:
        return SuppressionReason.NOTIFICATIONS_DISABLED
    local_time = evaluated_at.astimezone(ZoneInfo(preferences.timezone)).time().replace(tzinfo=None)
    if not _contains(preferences.awake_window, local_time):
        return SuppressionReason.OUTSIDE_AWAKE_WINDOW
    if preferences.quiet_window is not None and _contains(preferences.quiet_window, local_time):
        return SuppressionReason.QUIET_HOURS
    return None


def schedule_check_in(
    preferences: EnergyPreferences,
    consent: EnergyConsent,
    request: CheckInScheduleRequest,
) -> NotificationDecision:
    """Schedule one prompt only when consent and user timing policy allow it."""
    reason = _suppression_reason(preferences, consent, request.evaluated_at)
    if reason is not None:
        return _suppressed(request.check_in_id, reason)
    return ScheduledCheckIn(
        check_in_id=request.check_in_id,
        scheduled_at=request.evaluated_at,
        respond_by=request.evaluated_at + timedelta(minutes=preferences.response_window_minutes),
    )


def mark_missed(prompt: ActiveCheckIn, evaluated_at: datetime) -> ActiveCheckIn | MissedCheckIn:
    """Resolve expiry at the exact response deadline without catch-up inference."""
    if evaluated_at <= prompt.respond_by:
        return prompt
    return MissedCheckIn(
        check_in_id=prompt.check_in_id,
        missed_at=prompt.respond_by,
        snooze_count=prompt.snooze_count,
    )


def respond_to_check_in(
    prompt: NotificationDecision,
    response: EnergyResponse,
    consent: EnergyConsent,
) -> CheckInActionResult:
    """Record an explicit 1-5 response while current consent remains active."""
    match prompt:  # noqa: MATCH_OK - basedpyright proves union exhaustiveness.
        case SuppressedNotification():
            return prompt
        case ScheduledCheckIn() | SnoozedCheckIn():
            if not consent.granted or consent.decided_at > response.responded_at:
                return _suppressed(prompt.check_in_id, SuppressionReason.CONSENT_REQUIRED)
            resolved = mark_missed(prompt, response.responded_at)
            if isinstance(resolved, MissedCheckIn):
                return resolved
            return CompletedCheckIn(
                check_in_id=prompt.check_in_id,
                observation=EnergyObservation(
                    check_in_id=prompt.check_in_id,
                    rating=response.rating,
                    observed_at=response.responded_at,
                    provenance_id=f"notification:{prompt.check_in_id}",
                ),
                snooze_count=prompt.snooze_count,
            )


def snooze_check_in(
    prompt: NotificationDecision,
    request: SnoozeRequest,
) -> SnoozedCheckIn | MissedCheckIn | SuppressedNotification:
    """Defer an active prompt once using only configured deterministic timing."""
    match prompt:  # noqa: MATCH_OK - basedpyright proves union exhaustiveness.
        case SuppressedNotification():
            return prompt
        case ScheduledCheckIn() | SnoozedCheckIn():
            reason = _suppression_reason(request.preferences, request.consent, request.acted_at)
            if reason is not None:
                return _suppressed(prompt.check_in_id, reason)
            resolved = mark_missed(prompt, request.acted_at)
            if isinstance(resolved, MissedCheckIn):
                return resolved
            scheduled_at = request.acted_at + timedelta(minutes=request.preferences.snooze_minutes)
            target_reason = _suppression_reason(
                request.preferences,
                request.consent,
                scheduled_at,
            )
            if target_reason is not None:
                return MissedCheckIn(
                    check_in_id=prompt.check_in_id,
                    missed_at=scheduled_at,
                    snooze_count=prompt.snooze_count + 1,
                )
            return SnoozedCheckIn(
                check_in_id=prompt.check_in_id,
                scheduled_at=scheduled_at,
                respond_by=scheduled_at
                + timedelta(minutes=request.preferences.response_window_minutes),
                snooze_count=prompt.snooze_count + 1,
            )
