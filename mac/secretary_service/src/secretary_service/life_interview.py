"""Resumable, evidence-aware life interview over the governed Life Model."""

import re
from datetime import datetime, time
from typing import Literal, final
from uuid import NAMESPACE_URL, uuid4, uuid5

from secretary_service.interview_catalog import SENSITIVE_DOMAINS, TOPICS, WEEK_PLANNING_TOPICS
from secretary_service.life_knowledge import (
    InterviewProgress,
    KnowledgeDetails,
    KnowledgeKind,
    KnowledgeModel,
    KnowledgeState,
    LifeDomain,
    OpenLoopDetails,
    RoutineDetails,
    RoutineDraft,
    RoutineFlexibility,
    Sensitivity,
)
from secretary_service.life_model import MIN_CONFIDENCE, LifeModel
from secretary_service.memory import (
    MemoryCategory,
    MemoryCorrection,
    MemoryDeletion,
    MemoryProvenance,
    MemoryRecord,
    MemorySource,
    RetrievalScope,
    StaleMemoryRevisionError,
)
from secretary_service.memory_repository import MemoryRepository
from secretary_service.models import RecordId, TransitionContext

MAX_ANSWER_CHARACTERS = 8000
HOURS_PER_HALF_DAY = 12
MINUTES_PER_HOUR = 60
MINUTES_PER_DAY = 1440
HOURS_PER_DAY = 24

# Deterministic routine extraction: no LLM, no free-text inference. Only
# explicit keywords and patterns become structured planning knowledge, and the
# confirmation turn is what promotes a parse from pending to confirmed.
_DAY_NAMES_BY_INDEX: dict[int, str] = {
    0: "Monday",
    1: "Tuesday",
    2: "Wednesday",
    3: "Thursday",
    4: "Friday",
    5: "Saturday",
    6: "Sunday",
}
_DAY_INDEX_BY_NAME: dict[str, int] = {
    name.casefold(): index for index, name in _DAY_NAMES_BY_INDEX.items()
}
_DAY_PATTERN = re.compile(r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b")
_DAY_ALIAS_PATTERNS: tuple[tuple[re.Pattern[str], frozenset[int]], ...] = (
    (re.compile(r"\bweekdays?\b"), frozenset({0, 1, 2, 3, 4})),
    (re.compile(r"\bweekends?\b"), frozenset({5, 6})),
    (re.compile(r"\bdaily\b"), frozenset(range(7))),
    (re.compile(r"\bevery day\b"), frozenset(range(7))),
    (re.compile(r"\beveryday\b"), frozenset(range(7))),
    (re.compile(r"\beach day\b"), frozenset(range(7))),
    (re.compile(r"\ball week\b"), frozenset(range(7))),
    (re.compile(r"\bevery weekday\b"), frozenset({0, 1, 2, 3, 4})),
)
_TRAVEL_KEYWORDS = ("commute", "travel", "drive", "transit")
_PREPARATION_KEYWORDS = ("preparation", "prep", "getting ready")
_TRANSITION_KEYWORDS = ("transition", "wind down", "winding down", "decompress")
_CONFIRM_PHRASES = frozenset(
    {
        "yes",
        "yep",
        "yeah",
        "yup",
        "correct",
        "right",
        "exactly",
        "confirmed",
        "confirm",
        "that's right",
        "that is right",
        "that's correct",
        "that is correct",
        "sounds right",
        "sounds good",
        "looks good",
        "looks right",
        "sure",
        "ok",
        "okay",
        "fine",
        "good",
        "yes that's right",
        "yes correct",
        "yes that's correct",
        "that works",
        "works for me",
        "perfect",
    }
)


def _extract_days(text: str) -> frozenset[int] | None:
    if "except" in text:
        included, excluded = text.split("except", 1)
        base, exclusions = _extract_days(included), _extract_days(excluded)
        return frozenset(base - exclusions) if base and exclusions and base - exclusions else None
    days: set[int] = set()
    day = "|".join(_DAY_INDEX_BY_NAME)
    for interval in re.finditer(rf"\b({day})\s*(?:-|to|through)\s*({day})\b", text):
        first, last = (_DAY_INDEX_BY_NAME[name] for name in interval.groups())
        days.update((first + offset) % 7 for offset in range((last - first) % 7 + 1))
    for match in _DAY_PATTERN.finditer(text):
        days.add(_DAY_INDEX_BY_NAME[match.group(1)])
    for pattern, indices in _DAY_ALIAS_PATTERNS:
        if pattern.search(text):
            days.update(indices)
    return frozenset(days) if days else None


def _extract_start_time(text: str) -> time | None:
    match = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", text)
    if match:
        hour = int(match.group(1))
        minute = int(match.group(2) or 0)
        meridiem = match.group(3).casefold()
        if meridiem == "pm" and hour < HOURS_PER_HALF_DAY:
            hour += 12
        elif meridiem == "am" and hour == HOURS_PER_HALF_DAY:
            hour = 0
        if 1 <= int(match.group(1)) <= HOURS_PER_HALF_DAY and 0 <= minute < MINUTES_PER_HOUR:
            return time(hour, minute)
        return None
    match = re.search(r"\b(\d{1,2}):(\d{2})\b", text)
    if match:
        hour = int(match.group(1))
        minute = int(match.group(2))
        if 0 <= hour < HOURS_PER_DAY and 0 <= minute < MINUTES_PER_HOUR:
            return time(hour, minute)
    return None


def _extract_duration(text: str) -> int | None:
    # Time ranges are explicit elapsed durations, including overnight sleep.
    time_pattern = r"\d{1,2}(?::\d{2})?\s*(?:am|pm)"
    interval = re.search(rf"\b({time_pattern})\s*(?:-|\u2013|to)\s*({time_pattern})\b", text)
    if interval:
        start = _extract_start_time(interval.group(1))
        end = _extract_start_time(interval.group(2))
        if start is not None and end is not None:
            duration = (end.hour * 60 + end.minute - start.hour * 60 - start.minute) % 1440
            if duration:
                return duration
    # A commute/prep duration is not the activity's duration.
    pattern = r"\b(\d+(?:\.\d+)?)\s*(hours?|hrs?|minutes?|mins?)\b"
    for match in re.finditer(pattern, text):
        suffix = text[match.end() :].lstrip(" -")
        if suffix.startswith((*_TRAVEL_KEYWORDS, *_PREPARATION_KEYWORDS, *_TRANSITION_KEYWORDS)):
            continue
        value = float(match.group(1))
        unit = match.group(2)
        minutes = round(value * 60) if unit.startswith(("hour", "hr")) else round(value)
        if 1 <= minutes <= MINUTES_PER_DAY:
            return minutes
    return None


def _extract_minutes(text: str, keywords: tuple[str, ...]) -> int:
    for keyword in keywords:
        match = re.search(
            rf"\b(\d+)\s*(?:-|min(?:ute)?s?)?\s*(?:of\s+)?{re.escape(keyword)}\b",
            text,
        )
        if match:
            return int(match.group(1))
    return 0


def _extract_flexibility(text: str) -> RoutineFlexibility:
    if re.search(r"\b(must|fixed|always|non-negotiable)\b", text):
        return RoutineFlexibility.FIXED
    if re.search(r"\boptional\b", text):
        return RoutineFlexibility.OPTIONAL
    if re.search(r"\b(flexible|whenever|anytime|any time)\b", text):
        return RoutineFlexibility.FLEXIBLE
    return RoutineFlexibility.PREFERRED


class InterviewQuestion(KnowledgeModel):
    """One spoken or rendered prompt, identical across channels."""

    key: str
    domain: LifeDomain
    prompt: str
    mode: Literal["ask", "review", "permission", "confirm"] = "ask"
    evidence_ids: tuple[RecordId, ...] = ()


class DomainProgress(KnowledgeModel):
    """Understanding rather than an incentive to maximize disclosure."""

    domain: LifeDomain
    known: int
    total: int
    skipped: bool


class InterviewReply(KnowledgeModel):
    """Portable conversation response; no voice/channel state is persisted here."""

    session_id: RecordId
    revision: int
    phase: str
    question: InterviewQuestion | None
    progress: tuple[DomainProgress, ...]
    acknowledgment: str


@final
class LifeInterview:
    """Choose useful gaps and persist each answer with explicit provenance."""

    def __init__(self, memory: MemoryRepository, subject_id: str, *, timezone: str = "UTC") -> None:
        """Bind a server-authenticated canonical subject, never a caller-selected user."""
        self.memory = memory
        self.timezone = timezone
        self.subject_id = subject_id
        self.session_id = RecordId(uuid5(NAMESPACE_URL, f"lifeos:interview:{subject_id}"))

    def _session(self) -> MemoryRecord | None:
        return next(
            (
                r
                for r in self.memory.retrieve(RetrievalScope.PRIVATE)
                if r.memory_id == self.session_id and r.interview is not None
            ),
            None,
        )

    def begin(
        self,
        context: TransitionContext,
        *,
        objective: Literal["understanding", "week_planning"] | None = None,
    ) -> InterviewReply:
        """Resume current knowledge; never re-import or overwrite existing answers."""
        session = self._session()
        if session is None:
            session = MemoryRecord(
                memory_id=self.session_id,
                category=MemoryCategory.INTERVIEW,
                content="Know Me interview",
                provenance=self._provenance(context),
                confidence=1,
                retrieval_scopes=frozenset({RetrievalScope.PRIVATE}),
                created_at=context.occurred_at,
                interview=InterviewProgress(
                    subject_id=self.subject_id, objective=objective or "understanding"
                ),
            )
            self.memory.remember(session, context)
        elif (
            objective is not None
            and session.interview is not None
            and session.interview.objective != objective
        ):
            session = self.memory.correct(
                session.memory_id,
                MemoryCorrection(
                    expected_revision=session.revision,
                    content=session.content,
                    provenance=self._provenance(context),
                    confidence=1,
                    retrieval_scopes=session.retrieval_scopes,
                    interview=session.interview.model_copy(
                        update={"objective": objective, "phase": "active"}
                    ),
                ),
                context,
            )
        return self._reply(session, context.occurred_at, "We can take this at your pace.")

    def advance(
        self,
        expected_revision: int,
        action: Literal["answer", "skip", "pause", "resume"],
        text: str,
        context: TransitionContext,
        *,
        question_key: str | None = None,
    ) -> InterviewReply:
        """Save an answer and interview position atomically with optimistic concurrency."""
        with self.memory.transaction():
            session = self._session()
            if session is None or session.interview is None:
                msg = "start the interview before answering"
                raise ValueError(msg)
            if session.revision != expected_revision:
                raise StaleMemoryRevisionError(self.session_id, expected_revision, session.revision)
            state = session.interview
            updates: dict[str, object] = {}
            acknowledgment = "Saved."
            if action in {"pause", "resume"}:
                updates["phase"] = "paused" if action == "pause" else "active"
                acknowledgment = "We can pick this up whenever you're ready."
            else:
                if state.phase == "paused":
                    msg = "resume the interview before answering"
                    raise ValueError(msg)
                question = self._question(state, context.occurred_at)
                if question is None:
                    msg = "there is no pending interview question"
                    raise ValueError(msg)
                if question.key != question_key:
                    msg = "the pending question changed; refresh before answering"
                    raise ValueError(msg)
                updates, acknowledgment = self._respond(state, question, text, action, context)
            state = InterviewProgress.model_validate(state.model_dump() | updates)
            if state.phase != "paused" and self._question(state, context.occurred_at) is None:
                state = state.model_copy(update={"phase": "continuous"})
            updated = self.memory.correct(
                self.session_id,
                MemoryCorrection(
                    expected_revision=session.revision,
                    content=session.content,
                    provenance=self._provenance(context),
                    confidence=1,
                    retrieval_scopes=session.retrieval_scopes,
                    interview=state,
                ),
                context,
            )
            return self._reply(updated, context.occurred_at, acknowledgment)

    def _respond(  # noqa: C901, PLR0911 - explicit interview actions
        self,
        state: InterviewProgress,
        question: InterviewQuestion,
        text: str,
        action: str,
        context: TransitionContext,
    ) -> tuple[dict[str, object], str]:
        updates: dict[str, object] = {"last_question_key": question.key}
        if question.mode == "confirm":
            confirm_updates, acknowledgment = self._confirm(state, question, text, action, context)
            updates.update(confirm_updates)
            return updates, acknowledgment
        if action == "skip":
            updates["skipped_keys"] = state.skipped_keys | {question.key}
            if question.mode == "permission":
                updates["skipped_domains"] = state.skipped_domains | {question.domain}
            return updates, "Of course. We can leave that out."
        clean = text.strip()
        normalized = clean.casefold().rstrip(".!?")
        if question.mode == "permission":
            updates.update(self._permission(state, question.domain, normalized))
            return updates, "Saved."
        if not clean or len(clean) > MAX_ANSWER_CHARACTERS:
            message = "answer must contain between 1 and 8000 characters"
            raise ValueError(message)
        if question.key == "open_loops.else" and normalized in {
            "the sweep is complete",
            "sweep complete",
            "nothing else",
            "that's everything",
        }:
            updates["sweep_complete"] = True
            updates["skipped_keys"] = state.skipped_keys | {question.key}
            return updates, "Your inbox is ready for clarification whenever you are."
        pending = self._save_answer(question, clean, context)
        if question.key == "open_loops.known":
            updates["skipped_keys"] = state.skipped_keys | {question.key}
        if normalized in {"i don't know", "not sure", "unknown"}:
            updates["skipped_keys"] = state.skipped_keys | {question.key}
        elif pending:
            updates["pending_confirmation_key"] = question.key
        if question.key == "work.schedule":
            return updates, (
                "I noted your work schedule but couldn't automatically schedule it — "
                "you can use 'Plan My Week' to manually place it."
            )
        if question.key == "planning.fixed_commitments":
            return updates, (
                "I noted your fixed commitments but couldn't automatically schedule them — "
                "you can use 'Plan My Week' to manually place them."
            )
        if pending:
            return (
                updates,
                "I've saved that. Let me confirm the details before scheduling anything.",
            )
        return updates, (
            "I've put that in your inbox. We can clarify it before scheduling anything."
            if question.domain is LifeDomain.OPEN_LOOPS
            else "I've saved that."
        )

    @staticmethod
    def _permission(state: InterviewProgress, domain: LifeDomain, answer: str) -> dict[str, object]:
        if answer in {"yes", "yes please", "yes, please", "include it"}:
            return {"permitted_domains": state.permitted_domains | {domain}}
        if answer in {"no", "skip", "not now", "no thanks", "no, thanks"}:
            return {"skipped_domains": state.skipped_domains | {domain}}
        message = "please say yes or skip for this optional topic"
        raise ValueError(message)

    def _confirm(
        self,
        state: InterviewProgress,
        question: InterviewQuestion,
        text: str,
        action: str,
        context: TransitionContext,
    ) -> tuple[dict[str, object], str]:
        if action == "skip":
            self._resolve_pending(question, confirmed=False, context=context)
            return (
                {
                    "pending_confirmation_key": None,
                    "skipped_keys": state.skipped_keys | {question.key},
                },
                "Of course. I'll keep that as a note without scheduling it.",
            )
        clean = text.strip()
        if not clean or len(clean) > MAX_ANSWER_CHARACTERS:
            message = "answer must contain between 1 and 8000 characters"
            raise ValueError(message)
        normalized = clean.casefold().rstrip(".!?")
        if normalized in {"no", "no thanks", "not now", "don't plan this", "do not plan this"}:
            return self._confirm(state, question, "", "skip", context)
        if normalized in _CONFIRM_PHRASES:
            records = {r.memory_id: r for r in self.memory.retrieve(RetrievalScope.PRIVATE)}
            knowledge = records[question.evidence_ids[0]].knowledge
            pending = knowledge.pending_confirmation if knowledge is not None else None
            if pending is None or pending.missing():
                return {"pending_confirmation_key": question.key}, (
                    "I still need the missing scheduling details before you can confirm. "
                    "You can also skip and keep this as a note."
                )
            self._resolve_pending(question, confirmed=True, context=context)
            return (
                {"pending_confirmation_key": None},
                "I've confirmed your routine. Planning will respect its existing privacy limits.",
            )
        summary = self._reparse_pending(question, clean, context)
        return (
            {"pending_confirmation_key": question.key},
            (
                f"Got it. I understood: {summary}. Is this correct? "
                "Say 'yes' to confirm these details and allow planning, or tell me what to change."
            ),
        )

    def _resolve_pending(
        self,
        question: InterviewQuestion,
        *,
        confirmed: bool,
        context: TransitionContext,
    ) -> None:
        if not question.evidence_ids:
            return
        existing = {r.memory_id: r for r in self.memory.retrieve(RetrievalScope.PRIVATE)}
        record = existing.get(question.evidence_ids[0])
        if (
            record is None
            or record.knowledge is None
            or record.knowledge.pending_confirmation is None
        ):
            return
        details = record.knowledge
        pending = details.pending_confirmation
        if pending is None:
            return
        if confirmed:
            details = details.model_copy(
                update={
                    "planning_allowed": RetrievalScope.PLANNING in record.retrieval_scopes,
                    "routine": RoutineDetails.model_validate(pending.model_dump()),
                    "last_confirmed_at": context.occurred_at,
                    "pending_confirmation": None,
                }
            )
            confidence = 1.0
        else:
            details = details.model_copy(update={"pending_confirmation": None})
            confidence = record.confidence
        _ = self.memory.correct(
            record.memory_id,
            MemoryCorrection(
                expected_revision=record.revision,
                content=record.content,
                provenance=self._provenance(context, correction=True),
                confidence=confidence,
                retrieval_scopes=record.retrieval_scopes,
                knowledge=details,
            ),
            context,
        )

    def _reparse_pending(
        self,
        question: InterviewQuestion,
        text: str,
        context: TransitionContext,
    ) -> str:
        if not question.evidence_ids:
            return ""
        existing = {r.memory_id: r for r in self.memory.retrieve(RetrievalScope.PRIVATE)}
        record = existing.get(question.evidence_ids[0])
        if record is None or record.knowledge is None:
            return ""
        previous = record.knowledge.pending_confirmation
        fields = self._extract_routine_fields(text, context)
        if previous is not None:
            routine = RoutineDraft.model_validate(previous.model_dump() | fields)
        else:
            routine = self._build_routine(fields, context)
        details = record.knowledge.model_copy(update={"pending_confirmation": routine})
        _ = self.memory.correct(
            record.memory_id,
            MemoryCorrection(
                expected_revision=record.revision,
                content=record.content + "\nCorrection: " + text,
                provenance=self._provenance(context, correction=True),
                confidence=0.5,
                retrieval_scopes=record.retrieval_scopes,
                knowledge=details,
            ),
            context,
        )
        return self._routine_summary(routine)

    def _extract_routine_fields(self, text: str, context: TransitionContext) -> dict[str, object]:
        lowered = text.casefold()
        fields: dict[str, object] = {}
        days = _extract_days(lowered)
        if days is not None:
            fields["days"] = days
        start_time = _extract_start_time(lowered)
        if start_time is not None:
            fields["start_time"] = start_time
        duration = _extract_duration(lowered)
        if duration is not None:
            fields["duration_minutes"] = duration
        travel = _extract_minutes(lowered, _TRAVEL_KEYWORDS)
        if travel:
            fields["travel_minutes"] = travel
        preparation = _extract_minutes(lowered, _PREPARATION_KEYWORDS)
        if preparation:
            fields["preparation_minutes"] = preparation
        transition = _extract_minutes(lowered, _TRANSITION_KEYWORDS)
        if transition:
            fields["transition_minutes"] = transition
        flexibility = _extract_flexibility(lowered)
        if flexibility is not RoutineFlexibility.PREFERRED or "preferred" in lowered:
            fields["flexibility"] = flexibility
        fields["timezone"] = getattr(context, "timezone", None) or self.timezone
        return fields

    def _build_routine(self, fields: dict[str, object], context: TransitionContext) -> RoutineDraft:
        return RoutineDraft.model_validate(
            {"timezone": getattr(context, "timezone", None) or self.timezone} | fields
        )

    def _parse_routine(self, text: str, context: TransitionContext) -> RoutineDraft:
        return self._build_routine(self._extract_routine_fields(text, context), context)

    def _routine_summary(self, routine: RoutineDraft) -> str:
        day_names = ", ".join(_DAY_NAMES_BY_INDEX[i] for i in sorted(routine.days or ()))
        start_text = (
            routine.start_time.strftime("%I:%M %p").lstrip("0")
            if routine.start_time is not None
            else "unspecified time"
        )
        parts = [f"{day_names or 'unspecified days'} at {start_text}"]
        if routine.duration_minutes is not None:
            parts.append(f"{routine.duration_minutes} minutes")
        parts.extend(
            f"{minutes} minutes {name}"
            for name, minutes in (
                ("travel", routine.travel_minutes),
                ("preparation", routine.preparation_minutes),
                ("transition", routine.transition_minutes),
            )
            if minutes
        )
        parts.append(f"{routine.flexibility.value} timing; timezone {routine.timezone}")
        if routine.missing():
            parts.append("Still needed: " + ", ".join(routine.missing()))
        return "; ".join(parts)

    def _question(self, state: InterviewProgress, now: datetime) -> InterviewQuestion | None:  # noqa: C901 - evidence-aware question selection
        if state.phase == "paused":
            return None
        views = LifeModel(self.memory).knowledge(self.subject_id, RetrievalScope.PRIVATE, now)
        if state.pending_confirmation_key is not None:
            pending = next(
                (
                    v
                    for v in views
                    if v.record.knowledge is not None
                    and v.record.knowledge.key == state.pending_confirmation_key
                    and v.record.knowledge.pending_confirmation is not None
                ),
                None,
            )
            if (
                pending is not None
                and pending.record.knowledge is not None
                and pending.record.knowledge.pending_confirmation is not None
            ):
                topic = next(
                    t
                    for t in (*WEEK_PLANNING_TOPICS, *TOPICS)
                    if t.key == state.pending_confirmation_key
                )
                summary = self._routine_summary(pending.record.knowledge.pending_confirmation)
                return InterviewQuestion(
                    key=topic.key,
                    domain=topic.domain,
                    mode="confirm",
                    prompt=(
                        f"I understood: {summary}. Is this correct? "
                        "Say 'yes' to confirm these details and allow planning, "
                        "or tell me what to change."
                    ),
                    evidence_ids=(pending.record.memory_id,),
                )
        topics = WEEK_PLANNING_TOPICS if state.objective == "week_planning" else TOPICS
        for topic in topics:
            if topic.key in state.skipped_keys or topic.domain in state.skipped_domains:
                continue
            relevant = [
                v
                for v in views
                if v.record.knowledge is not None
                and v.record.knowledge.key == topic.key
                and v.record.knowledge.superseded_by is None
                and (v.current or v.record.knowledge.source_evidence is not None)
            ]
            if topic.key == "open_loops.known":
                relevant = [
                    v
                    for v in views
                    if v.record.knowledge is not None
                    and v.record.knowledge.kind is KnowledgeKind.OPEN_LOOP
                    and v.record.knowledge.superseded_by is None
                ]
                if topic.key == state.last_question_key or not relevant:
                    continue
            # Current explicit or connected evidence outranks imported summaries.
            authoritative = [
                v
                for v in relevant
                if v.current
                and v.record.provenance.source
                in {
                    MemorySource.USER_STATEMENT,
                    MemorySource.USER_CORRECTION,
                    MemorySource.CONNECTED_SYSTEM,
                }
                and v.state in {KnowledgeState.CONFIRMED, KnowledgeState.OBSERVED}
            ]
            if authoritative:
                relevant = authoritative
            private_topic = topic.domain in SENSITIVE_DOMAINS or any(
                v.record.knowledge is not None
                and v.record.knowledge.sensitivity is not Sensitivity.PERSONAL
                for v in relevant
            )
            if private_topic and topic.domain not in state.permitted_domains:
                introduction = f"May I use your saved {topic.domain.value} context? "
                return InterviewQuestion(
                    key=f"permission.{topic.domain.value}",
                    domain=topic.domain,
                    mode="permission",
                    prompt=introduction
                    + "It's optional. Say yes or skip; you can leave out any details.",
                )
            if (
                relevant
                and all(
                    v.current
                    and v.state is KnowledgeState.CONFIRMED
                    and v.record.confidence >= MIN_CONFIDENCE
                    for v in relevant
                )
                and (topic.key != "open_loops.else" or state.sweep_complete)
            ):
                continue
            if relevant and topic.key != "open_loops.else":
                return InterviewQuestion(
                    key=topic.key,
                    domain=topic.domain,
                    mode="review",
                    prompt="I'd like to check something rather than assume. "
                    + " ".join(
                        f"{v.explanation()} The note says: “{v.record.content}”."
                        for v in relevant[:3]
                    )
                    + " "
                    + topic.prompt,
                    evidence_ids=tuple(v.record.memory_id for v in relevant[:3]),
                )
            return InterviewQuestion(key=topic.key, domain=topic.domain, prompt=topic.prompt)
        return None

    def _save_answer(
        self,
        question: InterviewQuestion,
        text: str,
        context: TransitionContext,
    ) -> bool:
        topic = next(t for t in (*WEEK_PLANNING_TOPICS, *TOPICS) if t.key == question.key)
        unknown = text.casefold().rstrip(".!?") in {"i don't know", "not sure", "unknown"}
        details = KnowledgeDetails(
            subject_id=self.subject_id,
            key=topic.key,
            cardinality="many" if topic.kind is KnowledgeKind.OPEN_LOOP else "one",
            domain=topic.domain,
            kind=topic.kind,
            state=KnowledgeState.UNKNOWN if unknown else KnowledgeState.CONFIRMED,
            observed_at=context.occurred_at,
            last_confirmed_at=None if unknown else context.occurred_at,
            valid_from=context.occurred_at,
            expected_staleness_days=7 if topic.domain is LifeDomain.NOW else topic.staleness_days,
            sensitivity=Sensitivity.SENSITIVE
            if topic.domain in SENSITIVE_DOMAINS
            else Sensitivity.PERSONAL,
            importance=topic.importance,
            open_loop=OpenLoopDetails() if topic.kind is KnowledgeKind.OPEN_LOOP else None,
        )
        scopes = frozenset({RetrievalScope.PRIVATE, RetrievalScope.CONVERSATION})
        confidence = 0 if unknown else 1
        pending = False
        if topic.kind is KnowledgeKind.ROUTINE and not unknown:
            details = details.model_copy(
                update={
                    "planning_allowed": False,
                    "pending_confirmation": self._parse_routine(text, context),
                }
            )
            scopes = scopes | {RetrievalScope.PLANNING}
            confidence = 0.5
            pending = True
        elif topic.key in {"work.schedule", "planning.fixed_commitments"} and not unknown:
            details = details.model_copy(update={"planning_allowed": True})
            scopes = scopes | {RetrievalScope.PLANNING}
        if question.evidence_ids:
            existing = {r.memory_id: r for r in self.memory.retrieve(RetrievalScope.PRIVATE)}
            # Reviewing a non-imported private assertion must not widen its
            # retrieval scope or lower its classification either.
            classifications = {
                source.sensitivity
                for evidence_id in question.evidence_ids
                if (source := existing[evidence_id].knowledge) is not None
            }
            for evidence_id in question.evidence_ids:
                source_scopes = existing[evidence_id].retrieval_scopes
                if RetrievalScope.CONVERSATION in source_scopes:
                    source_scopes = source_scopes | {RetrievalScope.PLANNING}
                scopes = scopes & source_scopes
            if classifications & {Sensitivity.RESTRICTED, Sensitivity.SENSITIVE}:
                sensitivity = (
                    Sensitivity.RESTRICTED
                    if Sensitivity.RESTRICTED in classifications
                    else Sensitivity.SENSITIVE
                )
                details = details.model_copy(update={"sensitivity": sensitivity})
                scopes = frozenset({RetrievalScope.PRIVATE})
            if any(
                (source_details := existing[e].knowledge) is not None
                and source_details.source_evidence is not None
                for e in question.evidence_ids
            ):
                self._save_reconciliation(
                    question, text, details, scopes, existing, context, confidence
                )
                return pending
            first, *others = question.evidence_ids
            _ = self.memory.correct(
                first,
                MemoryCorrection(
                    expected_revision=existing[first].revision,
                    content=text,
                    provenance=self._provenance(context, correction=True),
                    confidence=confidence,
                    retrieval_scopes=scopes,
                    knowledge=details,
                ),
                context,
            )
            for memory_id in others:
                self.memory.delete(
                    memory_id,
                    MemoryDeletion(expected_revision=existing[memory_id].revision),
                    context,
                )
        else:
            self.memory.remember(
                MemoryRecord(
                    memory_id=RecordId(uuid4()),
                    category=MemoryCategory.PROFILE,
                    content=text,
                    provenance=self._provenance(context),
                    confidence=confidence,
                    retrieval_scopes=scopes,
                    created_at=context.occurred_at,
                    knowledge=details,
                ),
                context,
            )
        return pending

    def _save_reconciliation(  # noqa: PLR0913, PLR0917 - one atomic evidence replacement
        self,
        question: InterviewQuestion,
        text: str,
        details: KnowledgeDetails,
        scopes: frozenset[RetrievalScope],
        existing: dict[RecordId, MemoryRecord],
        context: TransitionContext,
        confidence: float,
    ) -> None:
        new_id = RecordId(uuid4())
        classifications = [
            evidence_details.sensitivity
            for e in question.evidence_ids
            if (evidence_details := existing[e].knowledge) is not None
        ]
        if Sensitivity.RESTRICTED in classifications:
            details = details.model_copy(update={"sensitivity": Sensitivity.RESTRICTED})
            scopes = frozenset({RetrievalScope.PRIVATE})
        elif Sensitivity.SENSITIVE in classifications:
            details = details.model_copy(update={"sensitivity": Sensitivity.SENSITIVE})
            scopes = frozenset({RetrievalScope.PRIVATE})
        # An ambiguous answer or inbox sweep does not resolve historical claims.
        supersedes = (
            details.state is KnowledgeState.CONFIRMED
            and details.kind is not KnowledgeKind.OPEN_LOOP
        )
        for evidence_id in question.evidence_ids:
            old = existing[evidence_id]
            if not supersedes or old.knowledge is None:
                continue
            _ = self.memory.correct(
                evidence_id,
                MemoryCorrection(
                    expected_revision=old.revision,
                    content=old.content,
                    provenance=old.provenance,
                    confidence=old.confidence,
                    retrieval_scopes=old.retrieval_scopes,
                    retain_until=old.retain_until,
                    knowledge=old.knowledge.model_copy(update={"superseded_by": new_id}),
                ),
                context,
            )
        self.memory.remember(
            MemoryRecord(
                memory_id=new_id,
                category=MemoryCategory.PROFILE,
                content=text,
                provenance=self._provenance(context, correction=True),
                confidence=confidence,
                retrieval_scopes=scopes,
                created_at=context.occurred_at,
                knowledge=details.model_copy(update={"evidence_ids": question.evidence_ids}),
            ),
            context,
        )

    def _reply(self, session: MemoryRecord, now: datetime, acknowledgment: str) -> InterviewReply:
        state = session.interview
        if state is None:
            msg = "invalid interview state"
            raise ValueError(msg)
        views = LifeModel(self.memory).knowledge(self.subject_id, RetrievalScope.PRIVATE, now)
        known = {
            v.record.knowledge.key
            for v in views
            if v.record.knowledge is not None
            and v.current
            and v.state is KnowledgeState.CONFIRMED
            and v.record.confidence >= MIN_CONFIDENCE
        }
        return InterviewReply(
            session_id=session.memory_id,
            revision=session.revision,
            phase=state.phase,
            question=self._question(state, now),
            acknowledgment=acknowledgment,
            progress=tuple(
                DomainProgress(
                    domain=domain,
                    known=sum(t.key in known for t in TOPICS if t.domain is domain),
                    total=sum(t.domain is domain for t in TOPICS),
                    skipped=domain in state.skipped_domains,
                )
                for domain in LifeDomain
            ),
        )

    @staticmethod
    def _provenance(context: TransitionContext, *, correction: bool = False) -> MemoryProvenance:
        return MemoryProvenance(
            source=MemorySource.USER_CORRECTION if correction else MemorySource.USER_STATEMENT,
            source_id=f"interview:{context.correlation_id}",
            captured_at=context.occurred_at,
        )
