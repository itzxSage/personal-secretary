"""Resumable, evidence-aware life interview over the governed Life Model."""

from datetime import datetime
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


class InterviewQuestion(KnowledgeModel):
    """One spoken or rendered prompt, identical across channels."""

    key: str
    domain: LifeDomain
    prompt: str
    mode: Literal["ask", "review", "permission"] = "ask"
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

    def __init__(self, memory: MemoryRepository, subject_id: str) -> None:
        """Bind a server-authenticated canonical subject, never a caller-selected user."""
        self.memory = memory
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

    def _respond(
        self,
        state: InterviewProgress,
        question: InterviewQuestion,
        text: str,
        action: str,
        context: TransitionContext,
    ) -> tuple[dict[str, object], str]:
        updates: dict[str, object] = {"last_question_key": question.key}
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
        self._save_answer(question, clean, context)
        if question.key == "open_loops.known":
            updates["skipped_keys"] = state.skipped_keys | {question.key}
        if normalized in {"i don't know", "not sure", "unknown"}:
            updates["skipped_keys"] = state.skipped_keys | {question.key}
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

    def _question(self, state: InterviewProgress, now: datetime) -> InterviewQuestion | None:
        if state.phase == "paused":
            return None
        views = LifeModel(self.memory).knowledge(self.subject_id, RetrievalScope.PRIVATE, now)
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
                    evidence_ids=tuple(v.record.memory_id for v in relevant),
                )
            return InterviewQuestion(key=topic.key, domain=topic.domain, prompt=topic.prompt)
        return None

    def _save_answer(
        self,
        question: InterviewQuestion,
        text: str,
        context: TransitionContext,
    ) -> None:
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
        if question.evidence_ids:
            existing = {r.memory_id: r for r in self.memory.retrieve(RetrievalScope.PRIVATE)}
            if any(
                (source_details := existing[e].knowledge) is not None
                and source_details.source_evidence is not None
                for e in question.evidence_ids
            ):
                self._save_reconciliation(question, text, details, scopes, existing, context)
                return
            first, *others = question.evidence_ids
            _ = self.memory.correct(
                first,
                MemoryCorrection(
                    expected_revision=existing[first].revision,
                    content=text,
                    provenance=self._provenance(context, correction=True),
                    confidence=0 if unknown else 1,
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
                    confidence=0 if unknown else 1,
                    retrieval_scopes=scopes,
                    created_at=context.occurred_at,
                    knowledge=details,
                ),
                context,
            )

    def _save_reconciliation(  # noqa: PLR0913, PLR0917 - one atomic evidence replacement
        self,
        question: InterviewQuestion,
        text: str,
        details: KnowledgeDetails,
        scopes: frozenset[RetrievalScope],
        existing: dict[RecordId, MemoryRecord],
        context: TransitionContext,
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
                confidence=0 if details.state is KnowledgeState.UNKNOWN else 1,
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
