#!/usr/bin/env python3
"""Read a user request from stdin and return a proposed plan; never apply it."""

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar
from uuid import uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError

from secretary_service.keys import KeyUnavailableError, MacOSKeychainKeyProvider
from secretary_service.planner import propose_day
from secretary_service.planner_models import DayPlanRequest
from secretary_service.slice.openai_interpreter import (
    HTTPSResponsesTransport,
    InterpretationProviderError,
    InterpretationSettings,
    OpenAIInterpretationAdapter,
)
from secretary_service.voice.privacy import RetentionVerification


class LiveInterpretationConfig(BaseModel):
    """Explicit local configuration; secret values must stay in Keychain."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")
    model: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    key_reference: str = Field(min_length=1)
    consent_until: AwareDatetime
    retention: RetentionVerification


class SystemClock:
    """UTC time for live consent and retention checks."""

    @staticmethod
    def now() -> datetime:
        """Return current UTC time."""
        return datetime.now(UTC)


def main() -> int:
    """Use configured provider authority only to interpret a planning proposal."""
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("--config", type=Path, required=True)
    _ = parser.add_argument("--base-plan", type=Path, required=True)
    args = parser.parse_args()
    try:
        config = LiveInterpretationConfig.model_validate_json(
            args.config.read_text(encoding="utf-8")
        )
        base_plan = DayPlanRequest.model_validate_json(args.base_plan.read_text(encoding="utf-8"))
        adapter = OpenAIInterpretationAdapter(
            settings=InterpretationSettings(
                model=config.model,
                project_id=config.project_id,
                key_reference=config.key_reference,
                retention=config.retention,
                consent_granted=lambda: datetime.now(UTC) < config.consent_until,
            ),
            clock=SystemClock(),
            keys=MacOSKeychainKeyProvider(),
            transport=HTTPSResponsesTransport(),
            base_plan=base_plan,
        )
        proposal = adapter.interpret(sys.stdin.read(16_001), uuid4())
        # Planner code, rather than the provider, computes the final schedule.
        day = propose_day(proposal.plan_request)
    except (OSError, ValidationError, KeyUnavailableError, InterpretationProviderError):
        print(
            "Interpretation unavailable: check input, Keychain, consent and project settings.",
            file=sys.stderr,
        )
        return 1
    print(day.model_dump_json(indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
