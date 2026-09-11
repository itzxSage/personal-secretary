"""SQLCipher final-transcript journal with fixed 180-day retention."""

from datetime import datetime
from pathlib import Path
from types import TracebackType
from typing import ClassVar, Self, final
from uuid import UUID

from pydantic import ConfigDict
from sqlcipher3 import dbapi2 as sqlcipher

from secretary_service.keys import KeyProvider
from secretary_service.models import FrozenModel, NonEmpty
from secretary_service.storage import Clock
from secretary_service.voice.errors import TranscriptStoreError, VoiceOrderError
from secretary_service.voice.models import TRANSCRIPT_RETENTION


def _require_cipher(row: tuple[str | bytes, ...] | None, path: Path) -> None:
    if row is None or not str(row[0]):
        raise TranscriptStoreError(path=str(path))


class TranscriptEntry(FrozenModel):
    """One encrypted final transcript; partials and audio are never persisted."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    session_id: UUID
    turn_id: UUID
    text: NonEmpty
    finalized_at: datetime
    expires_at: datetime


@final
class EncryptedTranscriptJournal:
    """Own the SQLCipher connection used only for final transcript text."""

    def __init__(self, path: Path, connection: sqlcipher.Connection, clock: Clock) -> None:
        """Bind an authenticated SQLCipher connection and lifecycle clock."""
        self.path = path
        self._connection = connection
        self._clock = clock

    @classmethod
    def open(cls, path: Path, keys: KeyProvider, clock: Clock) -> Self:
        """Open an authenticated SQLCipher transcript journal key-first."""
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlcipher.connect(str(path))
        try:
            key_hex = keys.database_key().hex()
            _ = connection.execute(f"PRAGMA key = \"x'{key_hex}'\"")
            cipher_row = connection.execute("PRAGMA cipher_version").fetchone()
            _require_cipher(cipher_row, path)
            _ = connection.execute("PRAGMA cipher_memory_security = ON")
            _ = connection.execute(
                """
                CREATE TABLE IF NOT EXISTS voice_transcripts (
                    session_id TEXT NOT NULL,
                    turn_id TEXT PRIMARY KEY,
                    final_text TEXT NOT NULL,
                    finalized_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                )
                """
            )
            connection.commit()
        except (sqlcipher.DatabaseError, TranscriptStoreError) as error:
            connection.close()
            raise TranscriptStoreError(path=str(path)) from error
        return cls(path, connection, clock)

    def __enter__(self) -> Self:
        """Return the open encrypted transcript journal."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close the SQLCipher connection on context exit."""
        self.close()

    def close(self) -> None:
        """Close the encrypted journal."""
        self._connection.close()

    def append_final(self, session_id: UUID, turn_id: UUID, text: str) -> TranscriptEntry:
        """Persist one final exactly once and reject conflicting duplicates."""
        existing = self._entry_for(turn_id)
        if existing is not None:
            if existing.text != text:
                raise VoiceOrderError(reason="conflicting final transcript")
            return existing
        now = self._clock.now()
        entry = TranscriptEntry(
            session_id=session_id,
            turn_id=turn_id,
            text=text,
            finalized_at=now,
            expires_at=now + TRANSCRIPT_RETENTION,
        )
        _ = self._connection.execute(
            "INSERT INTO voice_transcripts VALUES (?, ?, ?, ?, ?)",
            (
                str(entry.session_id),
                str(entry.turn_id),
                entry.text,
                entry.finalized_at.isoformat(),
                entry.expires_at.isoformat(),
            ),
        )
        self._connection.commit()
        return entry

    def entries(self) -> tuple[TranscriptEntry, ...]:
        """Return nonexpired final transcripts in finalization order."""
        rows = self._connection.execute(
            """SELECT session_id, turn_id, final_text, finalized_at, expires_at
            FROM voice_transcripts WHERE expires_at > ? ORDER BY finalized_at, turn_id""",
            (self._clock.now().isoformat(),),
        ).fetchall()
        return tuple(self._from_row(row) for row in rows)

    def latest_context(self, limit: int = 2) -> tuple[str, ...]:
        """Return only the latest bounded transcript snippets for provider context."""
        return tuple(entry.text[:256] for entry in self.entries()[-limit:])

    def purge_expired(self) -> int:
        """Delete final transcripts at the exact 180-day deadline."""
        row = self._connection.execute(
            "SELECT count(*) FROM voice_transcripts WHERE expires_at <= ?",
            (self._clock.now().isoformat(),),
        ).fetchone()
        expired = 0 if row is None else int(row[0])
        _ = self._connection.execute(
            "DELETE FROM voice_transcripts WHERE expires_at <= ?",
            (self._clock.now().isoformat(),),
        )
        self._connection.commit()
        return expired

    def column_names(self) -> tuple[str, ...]:
        """Return schema names for content-minimization verification."""
        rows = self._connection.execute("PRAGMA table_info(voice_transcripts)").fetchall()
        return tuple(str(row[1]) for row in rows)

    def _entry_for(self, turn_id: UUID) -> TranscriptEntry | None:
        row = self._connection.execute(
            """SELECT session_id, turn_id, final_text, finalized_at, expires_at
            FROM voice_transcripts WHERE turn_id = ?""",
            (str(turn_id),),
        ).fetchone()
        return None if row is None else self._from_row(row)

    @staticmethod
    def _from_row(row: tuple[str | bytes, ...]) -> TranscriptEntry:
        return TranscriptEntry(
            session_id=UUID(str(row[0])),
            turn_id=UUID(str(row[1])),
            text=str(row[2]),
            finalized_at=datetime.fromisoformat(str(row[3])),
            expires_at=datetime.fromisoformat(str(row[4])),
        )
