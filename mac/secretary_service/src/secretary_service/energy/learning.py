"""Local confidence-qualified energy pattern aggregation."""

from collections import defaultdict
from datetime import datetime
from typing import Final
from zoneinfo import ZoneInfo

from secretary_service.energy.models import EnergyObservation, EnergyPattern, TimeBucket

MINIMUM_PATTERN_SAMPLES: Final = 3
FULL_CONFIDENCE_SAMPLES: Final = 5
ENERGY_RANGE: Final = 4
MORNING_START: Final = 5
AFTERNOON_START: Final = 12
EVENING_START: Final = 17
NIGHT_START: Final = 22


def _bucket(observed_at: datetime, timezone: str) -> TimeBucket:
    hour = observed_at.astimezone(ZoneInfo(timezone)).hour
    if MORNING_START <= hour < AFTERNOON_START:
        return TimeBucket.MORNING
    if AFTERNOON_START <= hour < EVENING_START:
        return TimeBucket.AFTERNOON
    if EVENING_START <= hour < NIGHT_START:
        return TimeBucket.EVENING
    return TimeBucket.NIGHT


def learn_energy_patterns(
    observations: tuple[EnergyObservation, ...],
    timezone: str,
    learned_at: datetime,
) -> tuple[EnergyPattern, ...]:
    """Aggregate self-reports by local time without clinical interpretation."""
    grouped: dict[TimeBucket, list[EnergyObservation]] = defaultdict(list)
    for item in observations:
        grouped[_bucket(item.observed_at, timezone)].append(item)
    patterns: list[EnergyPattern] = []
    for bucket in TimeBucket:
        samples = grouped[bucket]
        if len(samples) < MINIMUM_PATTERN_SAMPLES:
            continue
        mean = sum(sample.rating for sample in samples) / len(samples)
        agreement = 1.0 - (
            sum(abs(sample.rating - mean) for sample in samples) / (len(samples) * ENERGY_RANGE)
        )
        sample_factor = min(1.0, len(samples) / FULL_CONFIDENCE_SAMPLES)
        patterns.append(
            EnergyPattern(
                bucket=bucket,
                expected_level=int(mean + 0.5),
                sample_count=len(samples),
                confidence=round(agreement * sample_factor, 6),
                provenance_ids=tuple(sorted(sample.provenance_id for sample in samples)),
                learned_at=learned_at,
            )
        )
    return tuple(patterns)
