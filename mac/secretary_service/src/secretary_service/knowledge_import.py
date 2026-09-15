"""Bounded provenance-bearing YAML ingestion into governed encrypted memory.

No imported assertion receives planning access. Source confirmation claims lacking
verifiable user provenance remain explicit source claims, pending reconciliation.
"""

import hashlib
import json
from collections.abc import Callable, Hashable, Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Literal, NoReturn, cast, override
from uuid import NAMESPACE_URL, uuid5

import yaml
from pydantic import JsonValue

from secretary_service.life_knowledge import (
    KnowledgeDetails,
    KnowledgeKind,
    KnowledgeSourceEvidence,
    KnowledgeState,
    LifeDomain,
    OpenLoopDetails,
    Sensitivity,
)
from secretary_service.memory import (
    MemoryCategory,
    MemoryProvenance,
    MemoryRecord,
    MemorySource,
    RetrievalScope,
)
from secretary_service.memory_repository import MemoryRepository
from secretary_service.models import RecordId, TransitionContext

MAX_SOURCE_BYTES = 1024 * 1024
MAX_ASSERTIONS = 2000
MAX_DEPTH = 20

# Schema labels only; personal values never live in the importer or its tests.
KEYS = {
    "subject.identity.preferred_name": "identity.name",
    "subject.identity.home_base": "identity.base",
    "career.current_employment.last_known": "work.role",
    "education": "education.program",
    "church_and_ministry": "values.commitments",
    "routines_and_wellbeing.last_known_targets.sleep": "routines.sleep",
    "routines_and_wellbeing.last_known_targets.workouts": "routines.exercise",
    "routines_and_wellbeing.last_known_targets.meals": "routines.meals",
}
UNCERTAINTY_KEYS = {
    "employment.current": "work.role",
    "work.schedule.next_7_days": "work.schedule",
    "education.current": "education.program",
    "home_base.current": "identity.base",
    "church.schedule.current": "values.commitments",
    "relationship_family.current": "relationships.people",
    "routines.current": "routines.weekly",
}
DOMAINS = {
    "subject": LifeDomain.IDENTITY,
    "values_and_priorities": LifeDomain.VALUES,
    "goals": LifeDomain.GOALS,
    "active_project_seeds": LifeDomain.PROJECTS,
    "lifeos_project": LifeDomain.PROJECTS,
    "career": LifeDomain.WORK,
    "education": LifeDomain.EDUCATION,
    "church_and_ministry": LifeDomain.VALUES,
    "relationships": LifeDomain.RELATIONSHIPS,
    "routines_and_wellbeing": LifeDomain.ROUTINES,
    "planning_preferences": LifeDomain.PLANNING,
    "technology": LifeDomain.LOGISTICS,
    "logistics": LifeDomain.LOGISTICS,
    "communication_preferences": LifeDomain.COMMUNICATION,
    "candidate_open_loops": LifeDomain.OPEN_LOOPS,
    "current_uncertainties": LifeDomain.NOW,
}


@dataclass(frozen=True)
class ImportSummary:
    """Content-free import receipt; safe to print."""

    source: str
    imported: int
    duplicates: int
    stale: int
    unknown: int
    sensitive: int
    confirmation_claims_pending: int


def _instant(value: JsonValue, fallback: datetime) -> datetime:
    if value is None:
        return fallback
    if not isinstance(value, str):
        _reject("invalid source timestamp")
    # Represent coarse dates by their lower boundary, with precision retained below.
    value += {4: "-01-01", 7: "-01"}.get(len(value), "")
    try:
        result = datetime.fromisoformat(value)
    except ValueError:
        _reject("invalid source timestamp")
    return result.replace(tzinfo=UTC) if result.tzinfo is None else result


def _atoms(
    node: JsonValue, path: str, inherited: dict[str, JsonValue], depth: int = 0
) -> Iterator[tuple[str, dict[str, JsonValue]]]:
    if depth > MAX_DEPTH:
        _reject("source nesting exceeds limit")
    if isinstance(node, dict):
        metadata = inherited | {
            k: v
            for k, v in node.items()
            if k
            in {
                "status",
                "confidence",
                "sensitivity",
                "source",
                "observed_at",
                "observed_as_of",
                "last_confirmed_at",
                "valid_from",
                "valid_until",
            }
        }
        if any(key in node for key in ("status", "value", "title", "employer")):
            yield path, inherited | node
            yield from _nested_claims(node, path, metadata, depth)
            return
        for key, value in node.items():
            if key not in metadata:
                yield from _atoms(value, f"{path}.{key}", metadata, depth + 1)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _atoms(value, f"{path}.{index}", inherited, depth + 1)
    elif node is not None:
        yield path, inherited | {"value": node}


def _nested_claims(
    node: dict[str, JsonValue], path: str, metadata: dict[str, JsonValue], depth: int
) -> Iterator[tuple[str, dict[str, JsonValue]]]:
    # Nested claims keep their own states, even under a confirmed parent.
    for key, value in node.items():
        if isinstance(value, dict):
            yield from _atoms(value, f"{path}.{key}", metadata, depth + 1)


def _content(path: str, original: dict[str, JsonValue]) -> str:
    # Keep review prompts short and avoid reciting incidental/private metadata.
    for key in ("value", "title"):
        if key in original:
            return str(original[key])
    for keys in (("employer", "role"), ("institution", "last_known_major"), ("organization",)):
        values = [str(original[k]) for k in keys if k in original]
        if values:
            return " — ".join(values)
    if original.get("status") == "UNKNOWN" and "key" in original:
        return "Needs reconciliation: " + str(original["key"]).replace("_", " ")
    return path.replace("_", " ") + ": " + json.dumps(original, ensure_ascii=False, default=str)


def _sensitivity(domain: LifeDomain, original: dict[str, JsonValue]) -> Sensitivity:
    if original.get("sensitivity") in {"high", "restricted"}:
        return Sensitivity.RESTRICTED
    if (
        original.get("sensitivity") == "sensitive"
        or domain
        in {
            LifeDomain.VALUES,
            LifeDomain.RELATIONSHIPS,
            LifeDomain.WELLNESS,
            LifeDomain.FINANCES,
        }
        or any(k in original for k in ("compensation", "pay", "benefits"))
    ):
        return Sensitivity.SENSITIVE
    return Sensitivity.PERSONAL


def _record(  # noqa: PLR0913, PLR0917 - explicit provenance inputs
    path: str,
    original: dict[str, JsonValue],
    subject_id: str,
    source_id: str,
    digest: str,
    generated: datetime,
    context: TransitionContext,
) -> MemoryRecord:
    raw_state = original.get("status")
    declared = KnowledgeState(str(raw_state).lower()) if raw_state is not None else None
    # Imported confirmation is a reported claim, not today's explicit user statement.
    state = (
        KnowledgeState.UNKNOWN
        if declared is None or declared is KnowledgeState.CONFIRMED
        else declared
    )
    domain = DOMAINS.get(path.split(".", maxsplit=1)[0], LifeDomain.TRUST)
    kind = KnowledgeKind.FACT
    if path.startswith("candidate_open_loops.items."):
        kind, domain = KnowledgeKind.OPEN_LOOP, LifeDomain.OPEN_LOOPS
    elif path.startswith("goals."):
        kind = KnowledgeKind.ASPIRATION
    elif path.startswith("active_project_seeds."):
        kind = KnowledgeKind.PROJECT
    elif path.startswith("current_uncertainties."):
        kind = KnowledgeKind.QUESTION
    elif "prior_roles" in path:
        kind = KnowledgeKind.HISTORY
    key = KEYS.get(path, "import." + path)
    if path.startswith("current_uncertainties."):
        key = UNCERTAINTY_KEYS.get(str(original.get("key")), key)
        if not key.startswith("import."):
            domain = LifeDomain(key.split(".")[0])
    observed = _instant(original.get("observed_at", original.get("observed_as_of")), generated)
    confidence = original.get("confidence", 0)
    if not isinstance(confidence, (float, int)) or isinstance(confidence, bool):
        _reject("invalid confidence")
    raw_observed = original.get("observed_at", original.get("observed_as_of"))
    precision: Literal["instant", "day", "month", "year", "source_snapshot"] = "source_snapshot"
    if isinstance(raw_observed, str):
        precision = cast(
            "Literal['instant', 'day', 'month', 'year']",
            {4: "year", 7: "month", 10: "day"}.get(len(raw_observed), "instant"),
        )
    evidence = KnowledgeSourceEvidence(
        source_id=source_id,
        source_path=path,
        document_sha256=digest,
        generated_at=generated,
        declared_state=declared,
        observation_precision=precision,
        original=original,
    )
    return MemoryRecord(
        memory_id=RecordId(uuid5(NAMESPACE_URL, f"lifeos:import:{subject_id}:{source_id}:{path}")),
        category=MemoryCategory.PROFILE,
        content=_content(path, original),
        provenance=MemoryProvenance(
            source=MemorySource.IMPORT, source_id=source_id, captured_at=context.occurred_at
        ),
        confidence=confidence,
        retrieval_scopes=frozenset({RetrievalScope.PRIVATE}),
        created_at=context.occurred_at,
        knowledge=KnowledgeDetails(
            subject_id=subject_id,
            key=key,
            cardinality="many",
            domain=domain,
            kind=kind,
            state=state,
            observed_at=observed,
            last_confirmed_at=(
                _instant(original["last_confirmed_at"], generated)
                if original.get("last_confirmed_at")
                and state not in {KnowledgeState.UNKNOWN, KnowledgeState.INFERRED}
                else None
            ),
            valid_from=_instant(original["valid_from"], generated)
            if original.get("valid_from")
            else None,
            valid_until=_instant(original["valid_until"], generated)
            if original.get("valid_until")
            else None,
            sensitivity=_sensitivity(domain, original),
            source_evidence=evidence,
            open_loop=OpenLoopDetails() if kind is KnowledgeKind.OPEN_LOOP else None,
        ),
    )


def _reject(reason: str) -> NoReturn:
    raise ValueError(reason)


def _json_default(value: object) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    _reject("unsupported source value")


class _StrictSafeLoader(yaml.SafeLoader):
    """SafeLoader subclass that rejects duplicate keys in YAML mappings."""

    @override
    def construct_mapping(
        self, node: yaml.MappingNode, deep: bool = False
    ) -> dict[Hashable, object]:
        # PyYAML exposes untyped node values; narrow them once at this boundary.
        nodes = cast("list[tuple[yaml.Node, yaml.Node]]", node.value)
        construct = cast("Callable[..., object]", self.construct_object)
        mapping: dict[Hashable, object] = {}
        for key_node, value_node in nodes:
            key = construct(key_node, deep=deep)
            if not isinstance(key, (str, int, float, bool, type(None))):
                _reject("unsupported YAML mapping key")
            if key in mapping:
                _reject("duplicate YAML key")
            mapping[key] = construct(value_node, deep=deep)
        return mapping


def _parse_document(raw: bytes) -> dict[str, JsonValue]:
    if not raw or len(raw) > MAX_SOURCE_BYTES:
        _reject("source size is invalid")
    # Aliases are unnecessary for the bounded export schema and can expand exponentially.
    scan = cast("Callable[[bytes], Iterator[yaml.tokens.Token]]", yaml.scan)
    if any(
        isinstance(token, (yaml.tokens.AliasToken, yaml.tokens.AnchorToken)) for token in scan(raw)
    ):
        _reject("YAML aliases are not supported")
    parsed = cast("object", yaml.load(raw, Loader=_StrictSafeLoader))  # noqa: S506 - SafeLoader subclass
    # Normalize YAML dates to ISO strings before strict domain validation.
    document = cast(
        "JsonValue",
        json.loads(
            json.dumps(
                parsed,
                default=_json_default,
            )
        ),
    )
    if not isinstance(document, dict):
        _reject("missing source schema")
    schema = document.get("schema")
    if not isinstance(schema, dict):
        _reject("missing source schema")
    if schema.get("name") != "LifeOSUserBootstrap" or schema.get("version") != "1.0":
        _reject("unsupported source schema")
    return document


def import_knowledge(
    raw: bytes,
    memory: MemoryRepository,
    subject_id: str,
    source_id: str,
    context: TransitionContext,
) -> ImportSummary:
    """Validate completely, then atomically import once per source assertion identity.

    Changed content under the same source ID fails closed; use a new version.
    Tombstoned identities are skipped so reruns cannot resurrect forgotten facts.
    All original attributes, including relationship references, remain encrypted
    on the corresponding assertion. No source file is needed by the runtime.

    .. note::
       Tombstone protection is per ``memory_id`` which includes ``source_id``.
       Passing a new ``--source-id`` (the documented upgrade path) creates new
       memory IDs for every assertion, so previously deleted facts will be
       re-imported.  To prevent resurrection, use the same ``--source-id`` for
       byte-identical re-imports only.  A future improvement could derive the
       identity from a per-assertion content digest instead of path + source_id.
    """
    document = _parse_document(raw)
    schema = cast("dict[str, JsonValue]", document["schema"])
    generated = _instant(schema.get("generated_at"), context.occurred_at)
    digest = hashlib.sha256(raw).hexdigest()
    records: list[MemoryRecord] = []
    for section, node in document.items():
        if section in {"schema", "knowledge_states"}:
            continue
        for path, original in _atoms(node, section, {}):
            records.append(
                _record(path, original, subject_id, source_id, digest, generated, context)
            )
            if len(records) > MAX_ASSERTIONS:
                _reject("too many source assertions")
    imported = duplicates = 0
    with memory.transaction():
        existing = {r.memory_id: r for r in memory.retrieve(RetrievalScope.PRIVATE)}
        for record in records:
            prior = existing.get(record.memory_id)
            if prior is not None:
                evidence = prior.knowledge.source_evidence if prior.knowledge else None
                if evidence is None or evidence.document_sha256 != digest:
                    _reject("source identity already exists with different content")
                duplicates += 1
            elif memory.tombstones(record.memory_id):
                duplicates += 1
            else:
                memory.remember(record, context)
                imported += 1
    details = [r.knowledge for r in records if r.knowledge is not None]
    return ImportSummary(
        source_id,
        imported,
        duplicates,
        sum(d.effective_state(context.occurred_at) is KnowledgeState.STALE for d in details),
        sum(d.state is KnowledgeState.UNKNOWN for d in details),
        sum(d.sensitivity is not Sensitivity.PERSONAL for d in details),
        sum(
            d.source_evidence is not None
            and d.source_evidence.declared_state is KnowledgeState.CONFIRMED
            for d in details
        ),
    )
