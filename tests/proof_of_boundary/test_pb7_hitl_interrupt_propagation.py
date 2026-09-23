# tests/proof_of_boundary/test_pb7_hitl_interrupt_propagation.py
#
# PB-7: HITL interrupt propagation tests.
# hitl.enabled: true in config/config.yaml — these tests are ACTIVE (not skipped).
#
# Two test cases (PB-7, HITL interrupt propagation):
#
#   test_pb7_hitl_interrupt_propagates()
#       Verifies that interrupt() raises GraphInterrupt and propagates through
#       BaseNode.__call__() to the LangGraph engine — NOT caught by the app boundary.
#
#   test_pb7_hitl_allowed_false_skips_interrupt()
#       Verifies that the hitl_allowed=False guard prevents interrupt() from
#       firing — no GraphInterrupt raised, no deadlock.

from __future__ import annotations

import pathlib
import warnings

import pytest

# ---------------------------------------------------------------------------
# Conditional skip — only runs when config/config.yaml has hitl.enabled: true
# ---------------------------------------------------------------------------

_CONFIG_PATH = pathlib.Path(__file__).parents[2] / "config" / "config.yaml"


def _hitl_enabled() -> bool:
    """Return True when config/config.yaml declares hitl.enabled: true."""
    if not _CONFIG_PATH.exists():
        warnings.warn(
            f"{_CONFIG_PATH} not found — PB-7 skipped without verifying hitl.enabled.",
            stacklevel=2,
        )
        return False
    try:
        import yaml

        data = yaml.safe_load(_CONFIG_PATH.read_text())
    except Exception as exc:
        warnings.warn(
            f"{_CONFIG_PATH} could not be read as YAML ({exc}) — PB-7 skipped.",
            stacklevel=2,
        )
        return False
    hitl = (data or {}).get("hitl", {}) if isinstance(data, dict) else None
    if not isinstance(hitl, dict):
        warnings.warn(
            f"{_CONFIG_PATH} does not have the expected 'hitl:' mapping shape — PB-7 skipped.",
            stacklevel=2,
        )
        return False
    return bool(hitl.get("enabled", False))


pytestmark = pytest.mark.skipif(
    not _hitl_enabled(),
    reason="config/config.yaml does not set hitl.enabled: true — PB-7 not applicable",
)


# ---------------------------------------------------------------------------
# Helper — build a minimal state for HitlReviewGateNode
# ---------------------------------------------------------------------------

def _base_state(**overrides) -> dict:
    """Return a minimal state dict for PB-7 tests."""
    state = {
        # Framework-managed fields
        "caller_trust_level": "ANONYMOUS",
        "correlation_id": "pb7-test",
        "node_history": [],
        "error_log": [],
        # HITL fields
        "hitl_allowed": True,
        "hitl_count": 0,
        # Domain fields expected by HitlReviewGateNode
        "briefing_draft": "## DRAFT\n\nTest briefing content for PB-7.",
        "review_reference": "REF-PB7TEST",
        "approval_decision": "",   # empty → node will try to interrupt
        "revision_count": 0,
        "rejection_feedback": "",
    }
    state.update(overrides)
    return state


# ---------------------------------------------------------------------------
# PB-7-A: interrupt() raises GraphInterrupt and propagates
# ---------------------------------------------------------------------------


def test_pb7_hitl_interrupt_propagates() -> None:
    """PB-7: interrupt() raises GraphInterrupt and propagates (not caught by app boundary).

    Covers PB-7 (first assertion):
      GraphInterrupt reaches the LangGraph engine; it is NOT swallowed by the
      application error boundary or converted to an error status.
    """
    from langgraph.errors import GraphInterrupt
    from src.nodes.hitl_review_gate_node import HitlReviewGateNode

    node = HitlReviewGateNode()
    state = _base_state(
        hitl_allowed=True,
        approval_decision="",   # no decision yet → must trigger interrupt()
    )

    with pytest.raises(GraphInterrupt):
        node(state)  # invoked via __call__(), not execute() directly


# ---------------------------------------------------------------------------
# PB-7-B: hitl_allowed=False guard prevents deadlock
# ---------------------------------------------------------------------------


def test_pb7_hitl_allowed_false_skips_interrupt() -> None:
    """PB-7 guard: hitl_allowed=False must NOT raise GraphInterrupt (no deadlock).

    Covers HITL compliance + review criterion #12:
      When hitl_allowed=False, the node checks the flag before calling
      interrupt() and skips the HITL path entirely.
    """
    from langgraph.errors import GraphInterrupt
    from framework.schemas.agent_status import AgentStatus
    from src.nodes.hitl_review_gate_node import HitlReviewGateNode

    node = HitlReviewGateNode()
    state = _base_state(
        hitl_allowed=False,
        approval_decision="",   # same trigger condition as PB-7-A
    )

    # Must NOT raise GraphInterrupt
    try:
        result = node(state)
    except GraphInterrupt:
        pytest.fail("GraphInterrupt was raised despite hitl_allowed=False — deadlock risk")

    # Node should return AWAITING_HUMAN (not success, not error) to signal that
    # the interrupt was deliberately skipped and a decision is still needed
    assert result.get("status") in (
        AgentStatus.AWAITING_HUMAN,
        AgentStatus.AWAITING_HUMAN.value,
    ), (
        f"Expected AWAITING_HUMAN when hitl_allowed=False, got: {result.get('status')!r}"
    )
