"""MainNode — unused scaffold stub kept for scaffold-integrity gate.

The EDU-C2-051 Cat 2 domain logic lives in DomainWorkflowGraphNode (graph.py)
and the five inner domain nodes.  This file is retained only because the
scaffold-integrity check expects it to exist under src/nodes/.
"""

from __future__ import annotations

from typing import ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event


class MainNode(FunctionNode):
    """Scaffold stub — not used in the Cat 2 pipeline."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: dict) -> dict:
        emit_trace_event("MainNode_execute_called", {"note": "scaffold stub"}, state)
        return {"status": AgentStatus.SUCCESS.value}
