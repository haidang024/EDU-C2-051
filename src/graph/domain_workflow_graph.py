"""DomainWorkflowGraph — inner BaseGraph for the Teacher PD Evidence workflow.

Pipeline:
    START → retrieval → mapping → analysis → briefing → hitl_gate → END
                ↓ (error)                                    ↓ (reject)
               END                                        briefing (revision loop)

All inner nodes use ANONYMOUS trust level (Cat 2 inner node rule).
"""

from __future__ import annotations

from langgraph.graph import END, START

from framework.graph.base_graph import BaseGraph
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from src.nodes.briefing_generation_node import BriefingGenerationNode
from src.nodes.gap_deadline_analysis_node import GapDeadlineAnalysisNode
from src.nodes.hitl_review_gate_node import HitlReviewGateNode
from src.nodes.record_retrieval_node import RecordRetrievalNode
from src.nodes.requirements_mapping_node import RequirementsMappingNode
from src.schemas.state import State


class DomainWorkflowGraph(BaseGraph):
    """Inner graph: Teacher PD Evidence multi-step domain workflow.

    Inherits BaseGraph directly for a fully custom topology.
    Called by DomainWorkflowGraphNode.get_subgraph() in graph.py.
    """

    @property
    def name(self) -> str:
        return "teacher_pd_evidence_workflow"

    @property
    def state_schema(self) -> type:
        return State

    def _validate_config(self) -> None:
        """No mandatory config keys for this inner graph."""
        pass

    def invoke(self, user_input, **kwargs):
        """Stash user_input so _extra_initial_state can unpack domain fields."""
        self._pending_user_input = user_input
        return super().invoke(user_input, **kwargs)

    def _extra_initial_state(self) -> dict:
        """Unpack domain fields from user_input JSON into top-level state keys.

        Inner nodes read teacher_id, cohort_filter, etc. directly from state.
        PreProcessNode serialises validated_input to JSON via extract_input();
        this hook restores those fields before the first inner node runs.
        """
        import json

        raw = getattr(self, "_pending_user_input", "")
        if raw and isinstance(raw, str) and raw.strip().startswith("{"):
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    allowed = {
                        "teacher_id",
                        "cohort_filter",
                        "reporting_period",
                        "institution_config",
                        "mode",
                        "approval_decision",
                        "stg_mock_mode",
                        "max_cohort_size",
                    }
                    return {key: value for key, value in parsed.items() if key in allowed}
            except json.JSONDecodeError:
                pass
        return {}

    def register_nodes(self) -> None:
        """Register all domain nodes. No super() — BaseGraph.register_nodes() is abstract."""
        self._nodes["retrieval"] = RecordRetrievalNode()
        self._nodes["mapping"] = RequirementsMappingNode()
        self._nodes["analysis"] = GapDeadlineAnalysisNode()
        self._nodes["briefing"] = BriefingGenerationNode()
        self._nodes["hitl_gate"] = HitlReviewGateNode()

    def add_edges(self) -> None:
        """Wire the domain topology with conditional routing."""
        self._sg.add_edge(START, "retrieval")

        # After retrieval: error → END, success → mapping
        self._sg.add_conditional_edges("retrieval", self._route_after_retrieval)

        # Linear: mapping → analysis → briefing
        self._sg.add_edge("mapping", "analysis")
        self._sg.add_edge("analysis", "briefing")

        # After briefing → hitl_gate
        self._sg.add_edge("briefing", "hitl_gate")

        # After hitl_gate: approved → END, rejected → briefing (revision loop)
        self._sg.add_conditional_edges("hitl_gate", self.route)

    def _route_after_retrieval(self, state: AgentState) -> str:
        """Route after retrieval: error → END, else → mapping."""
        status = state.get("status")
        if status == AgentStatus.ERROR.value or status == "error":
            return str(END)
        return "mapping"

    def route(self, state: AgentState) -> str:
        """Conditional routing after hitl_gate.

        - RETRY (reject) → briefing (revision loop)
        - SUCCESS (approve) → END
        - ERROR → END
        - Default → END
        """
        status = state.get("status")
        if status == AgentStatus.RETRY.value or status == "retry":
            return "briefing"
        return str(END)

    def get_output(self, state: AgentState) -> dict:
        """Shape the sub_result dict returned to the outer GraphNode.merge_output()."""
        return {
            "briefing_draft": state.get("briefing_draft", ""),
            "gap_analysis": state.get("gap_analysis", []),
            "cohort_roll_up": state.get("cohort_roll_up", {}),
            "approval_decision": state.get("approval_decision", ""),
            "briefing_citations": state.get("briefing_citations", []),
            "data_coverage_flags": state.get("data_coverage_flags", []),
            "requires_human_review": state.get("requires_human_review", True),
            "status": state.get("status"),
            # Why the workflow failed. Without this the outer GraphNode builds a
            # SubgraphError with an empty error_log, so on_subgraph_error() falls
            # back to a generic sentence and the real cause (an unprovisioned
            # connector credential) never reaches the caller or the logs.
            "error_log": state.get("error_log") or [],
            "trace_id": state.get("trace_id"),
            "correlation_id": state.get("correlation_id"),
            "node_history": state.get("node_history", []),
        }
