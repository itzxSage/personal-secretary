"""Deterministic provider, planner, and clock for Task 13 fixtures."""

from datetime import datetime
from typing import final

from secretary_service.authority import ProposalRecord, ProposalState
from secretary_service.voice.e2e_models import RealtimeVoiceFixture, VoiceE2EError
from secretary_service.voice.models import ProviderSessionConfig, ProviderSessionHandle


@final
class FixtureProvider:
    """Content-minimized realtime provider used by the relay fixture."""

    def __init__(self) -> None:
        """Initialize content-free fixture observations."""
        self.audio: list[bytes] = []
        self.cancelled = 0

    def start(self, config: ProviderSessionConfig) -> ProviderSessionHandle:
        """Start one deterministic provider session."""
        _ = config
        return ProviderSessionHandle(reference="task-13-provider-session")

    def send_audio(self, handle: ProviderSessionHandle, pcm: bytes) -> None:
        """Record audio received only through the owned relay."""
        _ = handle
        self.audio.append(pcm)

    def cancel_response(self, handle: ProviderSessionHandle) -> None:
        """Record provider response cancellation on barge-in."""
        _ = handle
        self.cancelled += 1

    def close(self, handle: ProviderSessionHandle) -> None:
        """Accept deterministic session closure."""
        _ = handle


@final
class FixturePlanningBackend:
    """Deterministic planner output applied only after Life Engine authorization."""

    def __init__(self, fixture: RealtimeVoiceFixture) -> None:
        """Bind validated planning outputs."""
        self._fixture = fixture
        self.applied = 0

    def rest_of_day(self) -> str:
        """Return the canonical fixture plan summary."""
        return self._fixture.rest_of_day_reply

    def propose_replan(self, text: str) -> ProposalRecord:
        """Return the fixture proposal only for its exact transcript."""
        if text != "move workout":
            gate = "replan transcript"
            raise VoiceE2EError(gate)
        return self._fixture.proposal

    def apply_replan(self, proposal: ProposalRecord) -> str:
        """Apply only an approved planner proposal."""
        if proposal.state is not ProposalState.APPROVED:
            gate = "approved proposal state"
            raise VoiceE2EError(gate)
        self.applied += 1
        return self._fixture.approved_reply


@final
class FixedClock:
    """Clock fixed to the relay fixture timestamp."""

    def __init__(self, current: datetime) -> None:
        """Fix all runtime decisions to one aware timestamp."""
        self._current = current

    def now(self) -> datetime:
        """Return the fixed timestamp."""
        return self._current


def require_gate(*, condition: bool, gate: str) -> None:
    """Raise a content-free error when one acceptance gate fails."""
    if not condition:
        raise VoiceE2EError(gate)
