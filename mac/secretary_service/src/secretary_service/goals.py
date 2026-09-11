"""Canonical Dream-to-Task goal graph persisted over SQLCipher."""

import hmac
from datetime import datetime
from enum import StrEnum
from typing import Literal, TypeVar, final, override

from pydantic import TypeAdapter, ValidationError
from sqlcipher3 import dbapi2 as sqlcipher

from secretary_service.audit import AuditMutation
from secretary_service.keys import KeyProvider
from secretary_service.ledger import AuditLedger
from secretary_service.models import FrozenModel, NonEmpty, RecordId, RecordKind, TransitionContext


class GoalNodeKind(StrEnum):
    """Closed variants in the canonical goal lineage."""

    DREAM = "dream"
    GOAL = "goal"
    MILESTONE = "milestone"
    PROJECT = "project"
    QUEST = "quest"
    TASK = "task"


class GoalNodeBase(FrozenModel):
    """Fields shared by canonical goal graph nodes."""

    record_id: RecordId
    created_at: datetime
    title: NonEmpty
    state: NonEmpty = "active"


class Dream(GoalNodeBase):
    """Long-horizon aspiration at the root of a goal graph."""

    kind: Literal[GoalNodeKind.DREAM] = GoalNodeKind.DREAM

    def parent(self) -> None:
        """Return no parent for the graph root."""
        return


class Goal(GoalNodeBase):
    """Outcome attached directly to a dream."""

    kind: Literal[GoalNodeKind.GOAL] = GoalNodeKind.GOAL
    dream_id: RecordId

    def parent(self) -> tuple[RecordId, GoalNodeKind]:
        """Return the required dream parent."""
        return self.dream_id, GoalNodeKind.DREAM


class Milestone(GoalNodeBase):
    """Measurable checkpoint attached to a goal."""

    kind: Literal[GoalNodeKind.MILESTONE] = GoalNodeKind.MILESTONE
    goal_id: RecordId

    def parent(self) -> tuple[RecordId, GoalNodeKind]:
        """Return the required goal parent."""
        return self.goal_id, GoalNodeKind.GOAL


class Project(GoalNodeBase):
    """Bounded body of work attached to a milestone."""

    kind: Literal[GoalNodeKind.PROJECT] = GoalNodeKind.PROJECT
    milestone_id: RecordId

    def parent(self) -> tuple[RecordId, GoalNodeKind]:
        """Return the required milestone parent."""
        return self.milestone_id, GoalNodeKind.MILESTONE


class Quest(GoalNodeBase):
    """Canonical motivating objective attached to a project."""

    kind: Literal[GoalNodeKind.QUEST] = GoalNodeKind.QUEST
    project_id: RecordId

    def parent(self) -> tuple[RecordId, GoalNodeKind]:
        """Return the required project parent."""
        return self.project_id, GoalNodeKind.PROJECT


class Task(GoalNodeBase):
    """Actionable work item attached to one canonical quest."""

    kind: Literal[GoalNodeKind.TASK] = GoalNodeKind.TASK
    quest_id: RecordId

    def parent(self) -> tuple[RecordId, GoalNodeKind]:
        """Return the required quest parent."""
        return self.quest_id, GoalNodeKind.QUEST


type GoalGraphNode = Dream | Goal | Milestone | Project | Quest | Task
GOAL_NODE_ADAPTER: TypeAdapter[GoalGraphNode] = TypeAdapter(GoalGraphNode)


class TaskExplanation(FrozenModel):
    """Task rationale anchored to its canonical quest and full lineage."""

    task: Task
    canonical_quest: Quest
    lineage: tuple[RecordId, ...]


@final
class GoalParentError(ValueError):
    """A goal node references a missing or incorrectly typed parent."""

    def __init__(self, child_kind: GoalNodeKind, parent_id: RecordId) -> None:
        """Initialize invalid parent details."""
        super().__init__(child_kind, parent_id)
        self.child_kind = child_kind
        self.parent_id = parent_id

    @override
    def __str__(self) -> str:
        return f"{self.child_kind.value} parent {self.parent_id} is not the required goal parent"


@final
class GoalNodeNotFoundError(Exception):
    """A requested canonical goal node does not exist."""

    def __init__(self, record_id: RecordId) -> None:
        """Initialize the missing node identifier."""
        super().__init__(record_id)
        self.record_id = record_id


_Node = TypeVar("_Node", bound=GoalNodeBase)


@final
class GoalGraphRepository:
    """Persist and resolve canonical goal graph lineage."""

    def __init__(
        self,
        connection: sqlcipher.Connection,
        keys: KeyProvider,
        ledger: AuditLedger,
    ) -> None:
        """Bind encrypted graph persistence and audit dependencies."""
        self._connection = connection
        self._keys = keys
        self._ledger = ledger

    def get(self, record_id: RecordId) -> GoalGraphNode | None:
        """Return one canonical goal graph node."""
        row = self._connection.execute(
            "SELECT content_json FROM goal_graph_nodes WHERE record_id=?", (str(record_id),)
        ).fetchone()
        return None if row is None else GOAL_NODE_ADAPTER.validate_json(str(row[0]))

    def _required(
        self,
        record_id: RecordId,
        model: type[_Node],
        child_kind: GoalNodeKind,
    ) -> _Node:
        row = self._connection.execute(
            "SELECT content_json FROM goal_graph_nodes WHERE record_id=?", (str(record_id),)
        ).fetchone()
        if row is None:
            raise GoalParentError(child_kind=child_kind, parent_id=record_id)
        try:
            return model.model_validate_json(str(row[0]))
        except ValidationError as error:
            raise GoalParentError(child_kind=child_kind, parent_id=record_id) from error

    def add(self, node: GoalGraphNode, context: TransitionContext) -> None:
        """Add one node after proving its required canonical parent."""
        _ = self._ledger.verify()
        parent = node.parent()
        if parent is not None:
            parent_id, expected_kind = parent
            found = self.get(parent_id)
            if found is None or found.kind is not expected_kind:
                raise GoalParentError(child_kind=node.kind, parent_id=parent_id)
        parent_id = None if parent is None else str(parent[0])
        _ = self._connection.execute(
            "INSERT INTO goal_graph_nodes VALUES(?, ?, ?, ?, ?)",
            (
                str(node.record_id),
                node.kind.value,
                parent_id,
                node.model_dump_json(),
                node.created_at.isoformat(),
            ),
        )
        fingerprint = hmac.digest(
            self._keys.audit_key(), node.model_dump_json().encode(), "sha256"
        ).hex()
        audit_kind = RecordKind.TASK if node.kind is GoalNodeKind.TASK else RecordKind.GOAL
        self._ledger.append(
            AuditMutation(
                record_kind=audit_kind,
                record_id=node.record_id,
                state=node.state,
                action_class=f"goal_graph.{node.kind.value}.created",
                source_fingerprint=fingerprint,
                context=context,
            )
        )
        self._connection.commit()

    def explain_task(self, task_id: RecordId) -> TaskExplanation:
        """Resolve a task through its canonical quest to its root dream."""
        try:
            task = self._required(task_id, Task, GoalNodeKind.TASK)
        except GoalParentError as error:
            raise GoalNodeNotFoundError(record_id=task_id) from error
        quest = self._required(task.quest_id, Quest, task.kind)
        lineage = self._lineage(quest)
        return TaskExplanation(
            task=task,
            canonical_quest=quest,
            lineage=(*lineage, task.record_id),
        )

    def _lineage(self, quest: Quest) -> tuple[RecordId, ...]:
        canonical_project = self._required(quest.project_id, Project, quest.kind)
        canonical_milestone = self._required(
            canonical_project.milestone_id,
            Milestone,
            canonical_project.kind,
        )
        canonical_goal = self._required(
            canonical_milestone.goal_id,
            Goal,
            canonical_milestone.kind,
        )
        canonical_dream = self._required(
            canonical_goal.dream_id,
            Dream,
            canonical_goal.kind,
        )
        return (
            canonical_dream.record_id,
            canonical_goal.record_id,
            canonical_milestone.record_id,
            canonical_project.record_id,
            quest.record_id,
        )
