from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel

from src.graph.graph import Graph


def test_invalid_marketplace_teacher_request_returns_readable_guidance():
    graph = Graph(config={})
    graph.compile()
    result = graph.invoke(
        "Hello",
        ctx=InvocationContext(caller_trust_level=TrustLevel.VERIFIED_EXTERNAL),
        input_context={"conversation_history": []},
    )
    assert result["status"] == "success"
    assert result["output"].startswith("Teacher evidence request could not be processed.")
    assert "failed validation" in result["output"]


def test_success_marketplace_output_is_readable_but_api_stays_structured():
    graph = Graph(config={})
    formatted_output = {
        "agent_id": "EDU-C2-051",
        "approval": {
            "decision": "approve",
            "reviewer_reference": "review-123",
            "revision_cycles": 1,
        },
        "briefing": {
            "content": "The evidence supports continued professional development in formative assessment.",
            "citations": ["PD Evidence Register 2026"],
            "status": "APPROVED",
        },
        "summary": {
            "gap_count": 1,
            "cohort_roll_up": {"teacher_count": 24},
            "data_coverage_flags": ["One school has partial coverage."],
        },
    }
    api_result = graph.get_output({"formatted_output": formatted_output})
    marketplace_result = graph.get_output(
        {"formatted_output": formatted_output, "input_context": {"conversation_history": []}}
    )
    assert api_result.get("output", api_result.get("formatted_output")) == formatted_output
    assert marketplace_result["output"].startswith("# Teacher Professional Development Evidence Briefing")
    assert "formative assessment" in marketplace_result["output"]
    assert isinstance(marketplace_result["output"], str)
