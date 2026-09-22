"""AI rule-authoring endpoints. The agent proposes; a human activates."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..agent import cases as case_matrix
from ..agent import orchestrator
from ..agent.providers import build_provider

router = APIRouter()
_pool = ThreadPoolExecutor(max_workers=4)


class AgentRequest(BaseModel):
    request: str = Field(..., min_length=4, max_length=4000)
    provider: str | None = None


@router.post("/agent/requests", tags=["agent"], summary="Turn a natural-language request into a reviewable draft")
def create_request(body: AgentRequest) -> dict:
    provider = None
    if body.provider:
        try:
            provider = build_provider(body.provider)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(422, f"provider unavailable: {exc}") from exc
    agent = orchestrator.RuleAuthoringAgent(provider=provider)
    # Run off the event loop: the provider client is synchronous.
    return _pool.submit(agent.run, body.request).result()


@router.get("/agent/requests", tags=["agent"])
def list_requests() -> dict:
    return {"requests": orchestrator.list_requests()}


@router.get("/agent/requests/{request_id}", tags=["agent"], summary="Draft, tool traces, latency and stop reason")
def get_request(request_id: str) -> dict:
    row = orchestrator.get_request(request_id)
    if row is None:
        raise HTTPException(404, "unknown request")
    return row


class Confirmation(BaseModel):
    actor: str = Field(..., min_length=1, description="Who is confirming; recorded in the audit trail")
    activate: bool = True
    edited_draft: dict | None = None


@router.post("/agent/requests/{request_id}/confirm", tags=["agent"],
             summary="Human confirmation - the only path from draft to active rule")
def confirm(request_id: str, body: Confirmation) -> dict:
    try:
        return orchestrator.confirm(request_id, actor=body.actor, activate=body.activate,
                                    edited_draft=body.edited_draft)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(422, f"draft failed server-side validation: {exc}") from exc


class Rejection(BaseModel):
    actor: str
    note: str = ""


@router.post("/agent/requests/{request_id}/reject", tags=["agent"])
def reject(request_id: str, body: Rejection) -> dict:
    return orchestrator.reject(request_id, body.actor, body.note)


@router.post("/agent/evaluate", tags=["agent"], summary="Run the repeatable case matrix")
def evaluate(provider: str | None = None) -> dict:
    return _pool.submit(case_matrix.run_matrix, provider).result()
