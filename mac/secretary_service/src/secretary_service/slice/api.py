"""Standalone FastAPI surface for the tomorrow planning slice."""

import os
import tempfile
from pathlib import Path
from typing import ClassVar, Final

from fastapi import FastAPI, HTTPException, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from secretary_service.authority import Approval, PolicyViolationError, Rollback
from secretary_service.keys import DeterministicTestKeyProvider
from secretary_service.models import RecordId
from secretary_service.slice.errors import (
    CaptureRejectedError,
    InterpretationNotFoundError,
    ProtectedConstraintViolationError,
    SliceProposalNotFoundError,
    UnsupportedCaptureError,
)
from secretary_service.slice.models import (
    ConversationCapture,
    ReplanCommand,
    TomorrowFixture,
)
from secretary_service.slice.runtime import build_fixture_runtime
from secretary_service.storage import EncryptedStateStore

ROOT: Final = Path(__file__).resolve().parents[5]
FIXTURES_DIR: Final = Path(os.environ.get("SECRETARY_SLICE_FIXTURES", str(ROOT / "fixtures")))
FIXTURE: Final = TomorrowFixture.model_validate_json(
    (FIXTURES_DIR / "tomorrow-plan.json").read_text(encoding="utf-8")
)
SANDBOX_PATH: Final = FIXTURES_DIR / "google" / "sandbox.json"
KEYS: Final = DeterministicTestKeyProvider.from_seed(b"task-8-tomorrow-slice")
_STORE_DIR: Final = Path(tempfile.mkdtemp(prefix="tomorrow-slice-"))
_STORE: Final = EncryptedStateStore.open(_STORE_DIR / "slice.sqlite", KEYS, FIXTURE.clock)
RUNTIME: Final = build_fixture_runtime(FIXTURE, SANDBOX_PATH, _STORE)

app = FastAPI(title="Tomorrow Planning Slice")


class ApproveRequest(BaseModel):
    """Device-signed approval bound to one in-flight proposal."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    proposal_id: RecordId
    approval: Approval


class RollbackRequest(BaseModel):
    """Payload-bound undo fact for one applied proposal."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    proposal_id: RecordId
    rollback: Rollback


def _json(payload: BaseModel) -> JSONResponse:
    """Serialize one slice result without computed fields."""
    return JSONResponse(content=payload.model_dump(mode="json", exclude_computed_fields=True))


def _preview(body: ConversationCapture) -> JSONResponse:
    """Interpret one capture and return the audited calendar preview."""
    try:
        return _json(RUNTIME.service.preview(body))
    except (CaptureRejectedError, UnsupportedCaptureError, InterpretationNotFoundError) as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error


@app.get("/health")
async def health() -> JSONResponse:
    """Report slice readiness for the E2E harness."""
    return JSONResponse(content={"status": "ok", "slice": "tomorrow-planning"})


@app.post("/capture")
async def capture(body: ConversationCapture) -> JSONResponse:
    """Interpret one capture and return the audited calendar preview."""
    return _preview(body)


@app.post("/preview")
async def preview(body: ConversationCapture) -> JSONResponse:
    """Alias of capture for callers that separate capture from preview."""
    return _preview(body)


@app.post("/approve")
async def approve(request: ApproveRequest) -> JSONResponse:
    """Approve and apply one payload-bound calendar proposal."""
    try:
        return _json(RUNTIME.service.approve(request.proposal_id, request.approval))
    except PolicyViolationError as error:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="approval rejected",
        ) from error
    except SliceProposalNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error


@app.post("/replan")
async def replan(command: ReplanCommand) -> JSONResponse:
    """Replan after a changed schedule while preserving protected intervals."""
    try:
        return _json(RUNTIME.service.replan(command))
    except (SliceProposalNotFoundError, ProtectedConstraintViolationError) as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(error),
        ) from error


@app.post("/rollback")
async def rollback(request: RollbackRequest) -> JSONResponse:
    """Revert one applied proposal and delete only its owned events."""
    try:
        return _json(RUNTIME.service.rollback(request.proposal_id, request.rollback))
    except SliceProposalNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error


@app.post("/cleanup")
async def cleanup(request: RollbackRequest) -> JSONResponse:
    """Idempotent E2E cleanup that removes every owned sandbox event."""
    try:
        return _json(RUNTIME.service.cleanup(request.proposal_id, request.rollback))
    except SliceProposalNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error
