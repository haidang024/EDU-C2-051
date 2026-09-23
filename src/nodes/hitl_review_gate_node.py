"""HitlReviewGateNode — Human-in-the-Loop review gate for briefing approval.

Inner domain node: required_trust_level = ANONYMOUS.
Raises GraphInterrupt when hitl_allowed=True; resumes from approval_decision.
Rejection loops back to briefing generation via domain_workflow_graph routing.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

# LangGraph interrupt mechanism
from langgraph.errors import GraphInterrupt
from langgraph.types import Interrupt


def interrupt(payload: dict) -> None:  # noqa: D103
    """Raise GraphInterrupt to pause execution for human review."""
    raise GraphInterrupt([Interrupt(value=payload)])


class HitlReviewGateNode(FunctionNode):
    """Gate briefing approval via human review.

    On first call: emits interrupt payload and raises GraphInterrupt so the
    LangGraph engine can checkpoint state and surface to the caller.

    On resume: reads approval_decision from state.
      - "approve": sets approval_timestamp, review_reference, status=SUCCESS
      - "reject":  stores rejection_feedback, increments revision_count,
                   returns status=RETRY so domain_workflow_graph routes back
                   to BriefingGenerationNode.

    Guard: interrupt() is only called when state.get("hitl_allowed", True) is True.
    When hitl_allowed=False the node skips the interrupt and returns the current
    approval_decision (or ERROR if none), preventing deadlock in batch/test contexts.
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: dict) -> dict:  # noqa: D102
        briefing_draft: str = state.get("briefing_draft", "")
        review_reference: str = state.get("review_reference", "")
        approval_decision: str = state.get("approval_decision", "")
        revision_count: int = state.get("revision_count", 0)
        hitl_count: int = state.get("hitl_count", 0)

        # ── Resume path: approval_decision already set by caller ─────────────
        if approval_decision in ("approve", "reject"):
            return self._handle_decision(state, approval_decision, review_reference, revision_count)

        # ── Interrupt path: request human review ─────────────────────────────
        emit_trace_event(
            "HitlReviewGateNode_review_requested",
            {
                "review_reference": review_reference,
                "revision_count": revision_count,
                "hitl_count": hitl_count,
                "briefing_line_count": len(briefing_draft.splitlines()),
            },
            state,
        )

        # CRITICAL guard: only interrupt when hitl_allowed=True (prevents deadlock
        # in batch/test/pipeline contexts where no human reviewer is available)
        if state.get("hitl_allowed", True):
            interrupt(
                {
                    "type": "briefing_review_requested",
                    "review_reference": review_reference,
                    "revision_count": revision_count,
                    "instructions": (
                        "Review the briefing draft. "
                        "Set approval_decision to 'approve' or 'reject'. "
                        "If rejecting, provide structured feedback in rejection_feedback."
                    ),
                    "briefing_preview": briefing_draft[:500] + ("..." if len(briefing_draft) > 500 else ""),
                }
            )
            # GraphInterrupt raised — execution does not reach here unless resumed
            # After resume, approval_decision will be set in state by the framework.
            # The framework re-invokes this node; the resume path above handles it.

        # hitl_allowed=False: cannot interrupt — return awaiting status without blocking
        return {
            "status": AgentStatus.AWAITING_HUMAN.value,
            "error_log": [
                "HitlReviewGateNode: hitl_allowed=False — interrupt skipped; "
                "set approval_decision manually to proceed"
            ],
        }

    # ── Decision handlers ─────────────────────────────────────────────────────

    def _handle_decision(
        self,
        state: dict,
        decision: str,
        review_reference: str,
        revision_count: int,
    ) -> dict:
        if decision == "approve":
            return self._approve(state, review_reference)
        return self._reject(state, revision_count)

    def _approve(self, state: dict, review_reference: str) -> dict:
        ts = datetime.now(timezone.utc).isoformat()
        emit_trace_event(
            "HitlReviewGateNode_review_complete",
            {
                "decision": "approve",
                "review_reference": review_reference,
                "approval_timestamp": ts,
            },
            state,
        )
        return {
            "approval_decision": "approve",
            "approval_timestamp": ts,
            "review_reference": review_reference,
            "status": AgentStatus.SUCCESS.value,
        }

    def _reject(self, state: dict, revision_count: int) -> dict:
        feedback = state.get("rejection_feedback", "No feedback provided.")
        new_revision_count = revision_count + 1
        emit_trace_event(
            "HitlReviewGateNode_review_complete",
            {
                "decision": "reject",
                "revision_count": new_revision_count,
                "feedback_length": len(feedback),
            },
            state,
        )
        return {
            "approval_decision": "reject",
            "rejection_feedback": feedback,
            "revision_count": new_revision_count,
            # RETRY signals domain_workflow_graph.route() to loop back to briefing
            "status": AgentStatus.RETRY.value,
        }
