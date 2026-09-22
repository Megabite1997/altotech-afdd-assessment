"""Rule-authoring orchestrator.

Keeps request state, calls bounded tools, inspects every result, and stops for a
declared reason. It never activates a rule: the only path from draft to active
is a human POSTing a confirmation, which is recorded with an actor and produces
a new immutable rule version.

Stop reasons: draft_ready | clarification_needed | unsupported | no_match |
              invalid_output | max_iterations | timeout | provider_error
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import datetime, timezone

from psycopg.types.json import Jsonb

from .. import db
from ..config import settings
from ..rules import store as rule_store
from ..rules.schema import validate_definition
from . import tools
from .providers import Provider, ProviderError, StubProvider, build_provider_with_fallback

log = logging.getLogger(__name__)

RETRYABLE_ATTEMPTS = 2


class RuleAuthoringAgent:
    def __init__(self, provider: Provider | None = None, max_iterations: int | None = None) -> None:
        fallback_reason = None
        if provider is None:
            provider, fallback_reason = build_provider_with_fallback()
        self.provider = provider
        self.fallback_reason = fallback_reason
        self.max_iterations = max_iterations or settings.agent_max_iterations

    # -- persistence --------------------------------------------------------

    def _create_request(self, request_id: str, text: str) -> None:
        db.execute(
            """
            INSERT INTO agent_request (request_id, request_text, status, provider, model, schema_version)
            VALUES (%s, %s, 'running', %s, %s, 'afdd-rule/1.0')
            """,
            (request_id, text, self.provider.name, self.provider.model),
        )

    def _record_step(self, request_id: str, seq: int, kind: str, name: str | None,
                     payload_in, payload_out, ok: bool, latency_ms: float) -> None:
        db.execute(
            """
            INSERT INTO agent_step (request_id, seq, kind, name, input, output, ok, latency_ms)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (request_id, seq, kind, name, Jsonb(payload_in), Jsonb(payload_out), ok, latency_ms),
        )

    def _finish(self, request_id: str, *, status: str, stop_reason: str, draft=None,
                preview=None, clarification=None, error=None, iterations: int = 0,
                latency_ms: float = 0.0) -> dict:
        db.execute(
            """
            UPDATE agent_request
               SET status = %s, outcome = %s, stop_reason = %s, draft = %s, preview = %s,
                   clarification = %s, error = %s, iterations = %s, latency_ms = %s, updated_at = now()
             WHERE request_id = %s
            """,
            (status, stop_reason, stop_reason, Jsonb(draft), Jsonb(preview), Jsonb(clarification),
             Jsonb(error), iterations, latency_ms, request_id),
        )
        return {
            "request_id": request_id,
            "status": status,
            "stop_reason": stop_reason,
            "draft": draft,
            "preview": preview,
            "clarification": clarification,
            "error": error,
            "iterations": iterations,
            "latency_ms": round(latency_ms, 1),
            "provider": self.provider.name,
            "model": self.provider.model,
            "provider_fallback_reason": self.fallback_reason,
        }

    # -- main loop ----------------------------------------------------------

    def run(self, request_text: str, request_id: str | None = None) -> dict:
        request_id = request_id or str(uuid.uuid4())
        self._create_request(request_id, request_text)
        started = time.monotonic()
        deadline = started + settings.agent_timeout_seconds

        messages: list[dict] = [{"role": "user", "content": request_text}]
        seq = 0
        last_preview = None

        for iteration in range(self.max_iterations):
            if time.monotonic() > deadline:
                return self._finish(request_id, status="stopped", stop_reason="timeout",
                                    error={"message": "agent exceeded its time budget"},
                                    iterations=iteration, latency_ms=(time.monotonic() - started) * 1000)

            try:
                result = self._provider_step(messages)
            except ProviderError as exc:
                self._record_step(request_id, seq, "provider_error", self.provider.model, None,
                                  {"error": str(exc)}, False, 0)
                return self._finish(request_id, status="failed", stop_reason="provider_error",
                                    error={"message": str(exc)}, iterations=iteration,
                                    latency_ms=(time.monotonic() - started) * 1000)
            seq += 1

            if not result.tool_calls:
                # The model answered in prose. That is not an actionable artefact.
                self._record_step(request_id, seq, "model_text", self.provider.model, None,
                                  {"text": result.text[:2000]}, False, 0)
                messages.append({"role": "assistant", "content": result.text or "(no content)"})
                messages.append({
                    "role": "user",
                    "content": "Prose is not an actionable draft. Call one of the available tools.",
                })
                seq += 1
                continue

            messages.append(self._assistant_message(result))
            tool_results = []
            terminal: tuple[str, dict] | None = None

            for callable_ in result.tool_calls:
                output, latency, ok = tools.call(callable_.name, callable_.arguments)
                seq += 1
                self._record_step(request_id, seq, "tool", callable_.name,
                                  callable_.arguments, output, ok, latency)
                tool_results.append((callable_, output, ok))
                if callable_.name == "preview_target" and output.get("valid"):
                    last_preview = output
                if output.get("terminal"):
                    terminal = (callable_.name, output)

            messages.append(self._tool_result_message(tool_results))

            if terminal:
                name, output = terminal
                elapsed = (time.monotonic() - started) * 1000
                if output["terminal"] == "draft_ready":
                    draft = next(c.arguments.get("rule") for c in result.tool_calls if c.name == name)
                    return self._finish(request_id, status="awaiting_confirmation",
                                        stop_reason="draft_ready", draft=draft, preview=output,
                                        iterations=iteration + 1, latency_ms=elapsed)
                if output["terminal"] == "no_match":
                    draft = next(c.arguments.get("rule") for c in result.tool_calls if c.name == name)
                    return self._finish(request_id, status="stopped", stop_reason="no_match",
                                        draft=draft, preview=output, iterations=iteration + 1,
                                        latency_ms=elapsed)
                if output["terminal"] == "clarification_needed":
                    return self._finish(request_id, status="needs_clarification",
                                        stop_reason="clarification_needed",
                                        clarification={"question": output["question"],
                                                       "options": output.get("options", [])},
                                        preview=last_preview, iterations=iteration + 1, latency_ms=elapsed)
                if output["terminal"] == "unsupported":
                    return self._finish(request_id, status="stopped", stop_reason="unsupported",
                                        error={"reason": output["reason"], "detail": output.get("detail", "")},
                                        iterations=iteration + 1, latency_ms=elapsed)

            # A submit_draft that failed validation returns terminal=None; the
            # errors are already in the tool result, so the model can correct.

        return self._finish(request_id, status="stopped", stop_reason="max_iterations",
                            preview=last_preview,
                            error={"message": f"did not converge within {self.max_iterations} iterations"},
                            iterations=self.max_iterations,
                            latency_ms=(time.monotonic() - started) * 1000)

    def _provider_step(self, messages: list[dict]):
        last: Exception | None = None
        for attempt in range(RETRYABLE_ATTEMPTS):
            try:
                return self.provider.step(tools.SYSTEM_PROMPT, messages, tools.SPECS)
            except ProviderError as exc:
                last = exc
                log.warning("provider call failed (attempt %d): %s", attempt + 1, exc)
                time.sleep(0.5 * (attempt + 1))
        raise last  # type: ignore[misc]

    def _assistant_message(self, result) -> dict:
        if isinstance(self.provider, StubProvider):
            return {"role": "assistant", "content": json.dumps(
                [{"tool": c.name, "arguments": c.arguments} for c in result.tool_calls])}
        content: list[dict] = []
        if result.text:
            content.append({"type": "text", "text": result.text})
        for call in result.tool_calls:
            content.append({"type": "tool_use", "id": call.call_id, "name": call.name,
                            "input": call.arguments})
        return {"role": "assistant", "content": content}

    def _tool_result_message(self, tool_results) -> dict:
        if isinstance(self.provider, StubProvider):
            return {"role": "user", "content": json.dumps(
                [{"tool": c.name, "result": o} for c, o, _ in tool_results], default=str)[:8000]}
        return {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": call.call_id,
                    "content": json.dumps(output, default=str)[:12000],
                    "is_error": not ok,
                }
                for call, output, ok in tool_results
            ],
        }


# ---------------------------------------------------------------------------
# human confirmation - the only path to activation
# ---------------------------------------------------------------------------

def confirm(request_id: str, *, actor: str, activate: bool = True,
            edited_draft: dict | None = None) -> dict:
    row = db.query_one("SELECT * FROM agent_request WHERE request_id = %s", (request_id,))
    if row is None:
        raise ValueError("unknown request")
    if row["status"] != "awaiting_confirmation":
        raise ValueError(f"request is {row['status']}, not awaiting confirmation")

    draft = edited_draft or row["draft"]
    definition = validate_definition(draft)  # server-side, never trusts the stored draft
    version = rule_store.save_version(definition, notes=f"agent request {request_id}",
                                      created_by=f"agent+{actor}")
    result = {"rule_key": definition.key, "version": version, "activated": False}
    if activate:
        rule_store.activate(definition.key, version, actor=actor, source="agent-confirmation")
        result["activated"] = True

    db.execute(
        """
        UPDATE agent_request
           SET status = 'confirmed', confirmed_by = %s, confirmed_at = %s,
               activated_rule_key = %s, activated_version = %s, draft = %s, updated_at = now()
         WHERE request_id = %s
        """,
        (actor, datetime.now(timezone.utc), definition.key, version,
         Jsonb(definition.model_dump(mode="json")), request_id),
    )
    return result


def reject(request_id: str, actor: str, note: str = "") -> dict:
    db.execute(
        """
        UPDATE agent_request SET status = 'rejected', confirmed_by = %s, confirmed_at = now(),
               error = %s, updated_at = now()
         WHERE request_id = %s
        """,
        (actor, Jsonb({"rejected_note": note}), request_id),
    )
    return {"request_id": request_id, "status": "rejected"}


def get_request(request_id: str) -> dict | None:
    row = db.query_one("SELECT * FROM agent_request WHERE request_id = %s", (request_id,))
    if row is None:
        return None
    row["steps"] = db.query(
        "SELECT seq, kind, name, input, output, ok, latency_ms, at FROM agent_step WHERE request_id = %s ORDER BY seq",
        (request_id,),
    )
    return row


def list_requests(limit: int = 50) -> list[dict]:
    return db.query(
        """
        SELECT request_id, request_text, status, stop_reason, provider, model, iterations,
               latency_ms, activated_rule_key, activated_version, created_at
          FROM agent_request ORDER BY created_at DESC LIMIT %s
        """,
        (limit,),
    )
