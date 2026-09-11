"""Optional OpenClaw channel projection of canonical Life Engine events."""

from dataclasses import dataclass

from secretary_service.channels.relay import TextChannelRelay


@dataclass(frozen=True, slots=True)
class OpenClawChannelBridge:
    """Expose only sequence replay through the existing typed adapter boundary."""

    relay: TextChannelRelay

    def synchronize(self, after_sequence: int) -> tuple[int, ...]:
        """Return canonical event sequences without retaining channel state."""
        return tuple(event.sequence for event in self.relay.synchronize(after_sequence).events)
