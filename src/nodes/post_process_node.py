"""PostProcessNode — formats the approved briefing for API response.

Outer Cat 2 node: required_trust_level = VERIFIED_EXTERNAL.
CRITICAL: must return "formatted_output" key — the API response depends on it.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event
from src.services.llm_runtime import provider_metadata, request_advisory


class PostProcessNode(FunctionNode):
    """Format and finalise the approved PD Evidence briefing for delivery.

    Reads briefing_draft, gap_analysis, cohort_roll_up, and approval metadata
    from state and assembles a structured formatted_output dict suitable for
    the API response layer.
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def _extra_security_gate_output(self, result: dict) -> dict:
        """S-3 domain hook: block credential-like values and unsupported compliance claims."""
        briefing = result.get("formatted_output", {})
        if not isinstance(briefing, dict):
            return result
        content = str(briefing.get("briefing", {}).get("content", ""))
        forbidden = ("password=", "api_key=", "token=", "Bearer ", "-----BEGIN")
        for pattern in forbidden:
            if pattern in content:
                from framework.errors import SecurityViolationError

                raise SecurityViolationError(f"PostProcessNode: output contains forbidden pattern: {pattern!r}")
        return result

    def __init__(self, llm: object | None = None, config: dict | None = None) -> None:
        super().__init__()
        self._llm = llm
        self._config = config or {}

    def execute(self, state: dict) -> dict:  # noqa: D102
        if state.get("input_error_message"):
            message = str(state["input_error_message"])
            return {"status": AgentStatus.SUCCESS.value, "result": message, "formatted_output": message}

        request_advisory(
            state,
            "Review the EDU-C2-051 result for clarity, grounding, and safe human review.",
            self._llm,
            timeout_s=float(self._config.get("timeout_s", 30.0)),
            max_retry=int(self._config.get("max_retry", 3)),
        )
        metadata = provider_metadata(state)
        briefing_draft: str = state.get("briefing_draft", "")
        gap_analysis: list = state.get("gap_analysis", [])
        cohort_roll_up: dict = state.get("cohort_roll_up", {})
        approval_decision: str = state.get("approval_decision", "")
        approval_timestamp: str = state.get("approval_timestamp", "")
        briefing_citations: list = state.get("briefing_citations", [])
        data_coverage_flags: list = state.get("data_coverage_flags", [])
        review_reference: str = state.get("review_reference", "")
        revision_count: int = state.get("revision_count", 0)

        # Build structured output — never expose raw credentials or PII
        formatted_output = {
            "agent_id": "EDU-C2-051",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "approval": {
                "decision": approval_decision,
                "timestamp": approval_timestamp,
                "reviewer_reference": review_reference,
                "revision_cycles": revision_count,
            },
            "briefing": {
                "content": briefing_draft,
                "citations": briefing_citations,
                "status": "APPROVED" if approval_decision == "approve" else "DRAFT",
            },
            "summary": {
                "gap_count": len(gap_analysis),
                "cohort_roll_up": cohort_roll_up,
                "data_coverage_flags": data_coverage_flags,
            },
        }

        # Inner workflow failed (e.g. an unavailable credential or connector).
        # Report it on a SUCCESS envelope: the Marketplace runner only forwards
        # `output` when status == "success", so status=error would leave the
        # caller with no reason at all.
        if state.get("workflow_error_message"):
            reason = str(state["workflow_error_message"])
            # `workflow_error_message` is already caller-safe text chosen from
            # a fixed table in graph.py. The raw cause lives on
            # workflow_error_detail and must not be rendered here.
            reference = str(state.get("workflow_error_code") or "WORKFLOW_FAILED")
            if reference == "REQUEST_INCOMPLETE":
                next_step = "- Add the missing details to your request and try again."
            else:
                next_step = "- Ask an administrator to finish this agent's setup, then retry."
            message = (
                "The teaching record briefing could not be completed.\n\n"
                f"Reason: {reason}\n\n"
                f"How to continue:\n{next_step}\n\n"
                f"Reference: {reference}"
            )
            return {
                "status": AgentStatus.SUCCESS.value,
                "result": message,
                "formatted_output": message,
            }

        emit_trace_event(
            "PostProcessNode_output_formatted",
            {
                "approval_decision": approval_decision,
                "gap_count": len(gap_analysis),
                "citation_count": len(briefing_citations),
                "coverage_flag_count": len(data_coverage_flags),
            },
            state,
        )

        return {
            "formatted_output": formatted_output,
            "status": AgentStatus.SUCCESS.value,
            **metadata,
        }
