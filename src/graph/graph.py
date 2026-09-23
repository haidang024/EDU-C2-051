"""Outer graph for EDU-C2-051 Teacher Professional Development Evidence Agent.

Architecture: Cat 2 — AgentBaseGraph (outer) + BaseGraph inner.
Pipeline: START → initialize → pre_process → main → post_process → finalize → END
The `main` slot is filled by DomainWorkflowGraphNode which wraps DomainWorkflowGraph.
"""

from __future__ import annotations


from typing import TYPE_CHECKING, Any, ClassVar, cast

from framework.graph.agent_base_graph import AgentBaseGraph
from framework.nodes.graph_node import GraphNode
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from shared.utils.audit_logger import emit_trace_event
from src.nodes.post_process_node import PostProcessNode
from src.nodes.pre_process_node import PreProcessNode
from src.schemas.state import State

if TYPE_CHECKING:
    from src.graph.domain_workflow_graph import DomainWorkflowGraph


# Caller-facing text per stable workflow error code. Raw node error_log text
# can carry a full traceback with absolute source paths and secret key names,
# so it must never be rendered into the chat window; the code is what support
# maps back to the detail kept on `workflow_error_detail`.
_WORKFLOW_ERROR_REASONS: dict[str, str] = {
    "CONFIGURATION_INVALID": (
        "This agent is not yet connected to the teaching-record systems it needs, " "so no briefing was produced."
    ),
    "REQUEST_INCOMPLETE": ("The request is missing details needed to build the briefing, so none was produced."),
    "OUTPUT_BLOCKED": ("The draft briefing did not pass the required content checks, so it was not released."),
    "WORKFLOW_FAILED": "The teaching record briefing could not be completed.",
}

_DEFAULT_WORKFLOW_ERROR_CODE = "WORKFLOW_FAILED"


def _classify_workflow_error(raw: str) -> str:
    """Map raw inner-graph error text onto a stable, caller-safe code."""
    lowered = raw.lower()
    if any(marker in lowered for marker in ("required secret", "missingsecret", "not found")):
        return "CONFIGURATION_INVALID"
    if any(marker in lowered for marker in ("is required", "not both", "must be", "prohibited characters")):
        return "REQUEST_INCOMPLETE"
    if "forbidden pattern" in lowered:
        return "OUTPUT_BLOCKED"
    return _DEFAULT_WORKFLOW_ERROR_CODE


def _user_facing_reason(error_code: str) -> str:
    """Return text that is safe to show the caller for a workflow error code."""
    return _WORKFLOW_ERROR_REASONS.get(error_code, _WORKFLOW_ERROR_REASONS[_DEFAULT_WORKFLOW_ERROR_CODE])


class DomainWorkflowGraphNode(GraphNode):
    """Wraps DomainWorkflowGraph; assigned to the `main` slot in Graph.

    propagate_hitl=True: HITL interrupts from the inner graph surface to the
    outer caller so the human reviewer can respond via the API.
    """

    # "handle" (not "propagate"): a propagated SubgraphError aborts the run
    # before merge_output(), so post_process never executes and the Marketplace
    # runner returns a bare RuntimeError with no reason. on_subgraph_error()
    # converts the failure into a domain field instead.
    error_strategy: ClassVar[str] = "handle"
    propagate_hitl: ClassVar[bool] = True

    def __init__(self, config: dict[str, Any] | None = None, llm: Any | None = None) -> None:
        super().__init__()
        self._config = dict(config or {})
        self._llm = llm
        self._subgraph: DomainWorkflowGraph | None = None

    def get_subgraph(self) -> DomainWorkflowGraph:  # noqa: D102
        from langgraph.checkpoint.memory import MemorySaver
        from src.graph.domain_workflow_graph import DomainWorkflowGraph

        # Keep one checkpointer-backed instance so a propagated HITL interrupt
        # can resume against the checkpoint created by the initial invocation.
        if self._subgraph is None:
            self._subgraph = DomainWorkflowGraph(config=self._parent_config())
            self._subgraph.compile(checkpointer=MemorySaver())
        return self._subgraph

    def extract_input(self, state: AgentState) -> str:  # noqa: D102
        import json

        # Prefer user_input (contains approval_decision + all domain fields).
        # Merge validated_input fields so inner nodes get both pre-validated data
        # and any pass-through fields like approval_decision.
        user_input_raw: str = state.get("user_input", "")
        validated: dict = state.get("validated_input") or {}
        if isinstance(validated, str):
            try:
                validated = json.loads(validated)
            except (json.JSONDecodeError, TypeError):
                validated = {}
        # Build merged payload: validated fields override raw, then layer on extras.
        merged: dict[str, Any] = {}
        if user_input_raw and user_input_raw.strip().startswith("{"):
            try:
                raw_parsed = json.loads(user_input_raw)
                if isinstance(raw_parsed, dict):
                    merged = {**raw_parsed, **validated}
            except (json.JSONDecodeError, TypeError):
                pass
        if not merged and validated:
            merged = dict(validated)
        # Node code has no graph-config back-reference. Carry only the runtime
        # values that inner nodes need as plain, checkpoint-safe state.
        merged["stg_mock_mode"] = bool(self._config.get("stg_mock_mode", False))
        merged["max_cohort_size"] = int(self._config.get("max_cohort_size", 50))
        if merged:
            return json.dumps(merged)
        return str(user_input_raw) if user_input_raw else ""

    def execute(self, state: AgentState) -> dict[str, Any]:
        if state.get("input_error_message"):
            return {"status": AgentStatus.SUCCESS.value}
        return cast(dict[str, Any], super().execute(state))

    def on_subgraph_error(self, state: AgentState, error: Exception) -> dict[str, Any]:
        """Carry an inner failure as a domain field so the pipeline keeps running.

        Returning status=error here would route straight to finalize, skipping
        post_process; the Marketplace runner then drops `output` and the caller sees
        only "invocation did not succeed".
        """
        error_log = getattr(error, "error_log", None) or []
        raw = "; ".join(str(e) for e in error_log if str(e).strip())
        # `raw` is node error_log text and can carry a full traceback with
        # absolute source paths and secret key names. It must never be rendered
        # into the chat window: only a pre-authored description keyed on a
        # stable code is shown, and the raw text stays on
        # workflow_error_detail, which is log-facing only.
        code = _classify_workflow_error(raw)
        return {
            "status": AgentStatus.SUCCESS.value,
            "workflow_error_code": code,
            "workflow_error_detail": raw,
            "workflow_error_message": _user_facing_reason(code),
        }

    def merge_output(self, state: AgentState, sub_result: dict) -> dict:  # noqa: D102
        """Map inner graph output fields back into the outer state.

        Returns only changed keys — never the full state.
        """
        emit_trace_event(
            "DomainWorkflowGraphNode_merge_output",
            {
                "approval_decision": sub_result.get("approval_decision", ""),
                "gap_count": len(sub_result.get("gap_analysis", [])),
                "has_briefing": bool(sub_result.get("briefing_draft")),
            },
            state,
        )
        return {
            "briefing_draft": sub_result.get("briefing_draft", ""),
            "gap_analysis": sub_result.get("gap_analysis", []),
            "cohort_roll_up": sub_result.get("cohort_roll_up", {}),
            "approval_decision": sub_result.get("approval_decision", ""),
            "briefing_citations": sub_result.get("briefing_citations", []),
            "data_coverage_flags": sub_result.get("data_coverage_flags", []),
            "requires_human_review": sub_result.get("requires_human_review", True),
            "status": sub_result.get("status"),
        }

    def _parent_config(self) -> dict:
        """Forward relevant config to the inner graph."""
        return {**self._config, "llm": self._llm}


class Graph(AgentBaseGraph):
    """Cat 2 outer graph — Teacher Professional Development Evidence Agent.

    Backbone: initialize → pre_process → main → post_process → finalize.
    Domain logic lives inside DomainWorkflowGraphNode (main slot).
    """

    @property
    def name(self) -> str:
        return "edu_c2_051"

    @property
    def state_schema(self) -> type:
        return State

    def register_nodes(self) -> None:
        super().register_nodes()  # fills: initialize, finalize (required)
        self._nodes["pre_process"] = PreProcessNode()
        self._nodes["main"] = DomainWorkflowGraphNode(
            config=self.config,
            llm=self.config.get("llm"),
        )
        self._nodes["post_process"] = PostProcessNode(
            llm=self.config.get("llm"),
            config=self.config,
        )

    def get_output(self, state: AgentState) -> dict[str, Any]:
        output = cast(dict[str, Any], super().get_output(state))
        output["generation_mode"] = state.get("generation_mode")
        output["provider_error_message"] = state.get("provider_error_message")
        # Classification + raw cause for operators. `workflow_error_detail` is
        # deliberately absent from the rendered `output` string — it can carry a
        # traceback and secret key names — but it must reach the audit log.
        output["workflow_error_code"] = state.get("workflow_error_code")
        output["workflow_error_detail"] = state.get("workflow_error_detail")
        context = state.get("input_context")
        is_marketplace = isinstance(context, dict) and "conversation_history" in context
        if not is_marketplace:
            return output

        if _set_marketplace_guidance(output, state, "Teacher evidence request"):
            return output

        payload = output.get("output", output.get("formatted_output"))
        if isinstance(payload, dict):
            output["output"] = self._render_marketplace_briefing(payload)
        return output

    @staticmethod
    def _render_marketplace_briefing(payload: dict[str, Any]) -> str:
        approval = payload.get("approval")
        approval = approval if isinstance(approval, dict) else {}
        briefing = payload.get("briefing")
        briefing = briefing if isinstance(briefing, dict) else {}
        summary = payload.get("summary")
        summary = summary if isinstance(summary, dict) else {}
        lines = [
            "# Teacher Professional Development Evidence Briefing",
            "",
            f"**Status:** {briefing.get('status', 'DRAFT')}",
            f"**Review decision:** {approval.get('decision') or 'pending'}",
            f"**Review reference:** {approval.get('reviewer_reference') or 'not provided'}",
            "",
            str(briefing.get("content", "No briefing content was generated.")),
            "",
            f"Evidence gaps: {summary.get('gap_count', 0)}",
        ]
        citations = briefing.get("citations")
        if isinstance(citations, list) and citations:
            lines.extend(["", "Sources:"])
            for citation in citations[:20]:
                if isinstance(citation, dict):
                    lines.append(f"- {citation.get('citation') or citation.get('source') or 'Source'}")
                else:
                    lines.append(f"- {citation}")
        flags = summary.get("data_coverage_flags")
        if isinstance(flags, list) and flags:
            lines.extend(["", "Data coverage notes:"])
            lines.extend(f"- {flag}" for flag in flags)
        cohort = summary.get("cohort_roll_up")
        if isinstance(cohort, dict) and cohort:
            lines.extend(["", "Cohort summary:"])
            lines.extend(f"- {key.replace('_', ' ').title()}: {value}" for key, value in cohort.items())
        return "\n".join(lines)

    # add_edges() is NOT overridden — backbone wiring belongs to the framework.


def _set_marketplace_guidance(output: dict[str, Any], state: AgentState, subject: str) -> bool:
    context = state.get("input_context")
    message = state.get("input_error_message")
    if not (isinstance(context, dict) and "conversation_history" in context and message):
        return False
    lines = [f"{subject} could not be processed.", "", f"Reason: {message}"]
    guidance = state.get("input_error_guidance")
    if isinstance(guidance, list) and guidance:
        lines.extend(["", "How to continue:"])
        lines.extend(f"- {item}" for item in guidance)
    output["output"] = "\n".join(lines)
    return True
