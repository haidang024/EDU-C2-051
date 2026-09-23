# Test Specification — EDU-C2-051 Teacher Professional Development Evidence Agent

## Test Strategy

- **Coverage target**: 85% line coverage (unit + proof-of-boundary combined)
- **Test types**: Unit (domain nodes) / Proof-of-Boundary (framework contracts) / Boundary-Layer (BL)
- **Runner**: `pytest` against the CI-provided AgentCore wheel
- **HITL**: tests are **active** because `config/config.yaml` sets `hitl.enabled: true`

---

## Framework Compliance Tests (TC series)

| TC-ID | Test | Expected Result | File |
|-------|------|----------------|------|
| TC-01 | State contract: flat TypedDict | `State` is a TypedDict subclass; no Pydantic, no dataclass | test_state_safety.py |
| TC-02 | S-1 denial on insufficient trust | Under-privileged caller is refused before `execute()` | test_pb_invoke_order.py::test_s1_denial_refuses_execution_before_execute |
| TC-03 | No credentials in State fields | State TypedDict has no jwt/token/api_key/secret/password/credential/connection_string fields | test_state_safety.py |
| TC-04 | InvocationContext via context only | Credentials accessed via `ctx.secrets.require()`, not from state dict | test_main_node.py (RecordRetrievalNode) |
| TC-05 | No duplicate lifecycle events in execute() | node_start / node_complete absent from execute() body of all nodes | test_import_isolation.py |
| TC-06 | `_security_gate_input()` is framework-final | Override attempt raises `TypeError` | test_framework_compliance_tc06_tc07.py |
| TC-07 | `_security_gate_output()` is framework-final | Override attempt raises `TypeError` | test_framework_compliance_tc06_tc07.py |
| TC-08 | required_trust_level enforced | Inner nodes = ANONYMOUS; outer pre/post = VERIFIED_EXTERNAL | test_pb_invoke_order.py |
| TC-09 | emit_trace_event called in every execute() | Each node emits at least one trace event | test_main_node.py (per-node) |
| TC-10 | execute() returns only changed keys | No node returns full state; spot-checked on all 7 nodes | test_main_node.py |
| TC-11 | PostProcessNode returns formatted_output | formatted_output key present in PostProcessNode result | test_main_node.py |

---

## Proof-of-Boundary Tests (PB series)

| PB-ID | Test | Expected Result | File |
|-------|------|----------------|------|
| PB-1 | Import isolation: no cross-layer imports | src/nodes/ does not import from src/api/ or mediator/ | test_import_isolation.py |
| PB-2 | State is not mutated by nodes | `state["key"] = val` patterns absent; nodes return delta dicts | test_state_safety.py |
| PB-3 | No __call__ override in any node | None of the 7 domain nodes define __call__ | test_import_isolation.py |
| PB-4 | No _invoke_impl in any node | Domain nodes use execute() not _invoke_impl | test_import_isolation.py |
| PB-5 | Checkpoint safety *(conditional)* | When framework ingress hooks are available, inspect checkpoint, metadata, and pending writes; otherwise auto-waived pending cutover | test_state_safety.py |
| PB-6 | Invoke order: S-1→node_start→S-2→execute→S-3→node_complete | All concrete nodes in src/nodes/ pass order check | test_pb_invoke_order.py::test_call_order_for_every_node |
| PB-6 (neg) | S-1 rejects insufficient trust | Dedicated privileged fixture proves denial before execute or normal lifecycle events | test_pb_invoke_order.py::test_s1_denial_refuses_execution_before_execute |
| PB-7 | HITL interrupt propagates | HitlReviewGateNode with hitl_allowed=True raises GraphInterrupt | test_pb7_hitl_interrupt_propagation.py::test_pb7_hitl_interrupt_propagates |
| PB-7 (guard) | hitl_allowed=False skips interrupt | No GraphInterrupt; returns AWAITING_HUMAN | test_pb7_hitl_interrupt_propagation.py::test_pb7_hitl_allowed_false_skips_interrupt |
| PB-8 | Standalone adapter injection/auth | Key-less boot succeeds; optional LLM reaches Cat-2 wrapper/inner config; bearer trust never over-promotes | test_server_llm_injection.py |

---

## Domain Unit Tests (DU series)

### RecordRetrievalNode

| DU-ID | Test | State Input | Expected Output |
|-------|------|-------------|-----------------|
| DU-RR-01 | Valid teacher_id | `teacher_id="TCH-001"` | evidence_records non-empty, status=SUCCESS |
| DU-RR-02 | Valid cohort_filter | `cohort_filter={"department":"science","school":"Lincoln"}` | evidence_records non-empty, multiple refs |
| DU-RR-03 | Missing both | `teacher_id="", cohort_filter={}` | status=ERROR, error_log contains "required" |
| DU-RR-04 | Both provided | `teacher_id="TCH-001", cohort_filter={"department":"math"}` | status=ERROR, error_log contains "both" |
| DU-RR-05 | No PII in records | Any valid input | evidence_records has no name/email/dob/address fields |
| DU-RR-06 | Pseudonymous reference | `teacher_id="TCH-REAL-001"` | review_reference != "TCH-REAL-001" |

### RequirementsMappingNode

| DU-ID | Test | State Input | Expected Output |
|-------|------|-------------|-----------------|
| DU-RM-01 | Maps to categories | standard evidence_records | mapped_requirements non-empty, status=SUCCESS |
| DU-RM-02 | Citations attached | standard input | each item has citations list and requirement_citation |
| DU-RM-03 | Hours aggregated | 6h LMS record for instructional_technology | earned_hours=6.0 for that ref+category |
| DU-RM-04 | Institution override | institution_config with custom schedule | custom category appears in mapped_requirements |

### GapDeadlineAnalysisNode

| DU-ID | Test | State Input | Expected Output |
|-------|------|-------------|-----------------|
| DU-GA-01 | Identifies gap | earned=6 < required=10 | is_gap=True, shortfall_hours=4.0 |
| DU-GA-02 | No gap when met | earned=4, required=4 | is_gap=False |
| DU-GA-03 | Urgency values valid | any mapped_requirements | urgency in {red,amber,green,unknown} for all items |
| DU-GA-04 | requires_human_review=True | any input | requires_human_review always True |
| DU-GA-05 | Cohort roll-up present | standard input | total_teachers, teachers_with_gaps, urgency_counts present |
| DU-GA-06 | Empty input graceful | mapped_requirements=[] | status=SUCCESS, gap_analysis=[], total_teachers=0 |

### BriefingGenerationNode

| DU-ID | Test | State Input | Expected Output |
|-------|------|-------------|-----------------|
| DU-BG-01 | Produces Markdown draft | standard gap_analysis | briefing_draft is str, len>100, contains "DRAFT" |
| DU-BG-02 | No PII in draft | gap_analysis with injected _raw_name | "Jane Smith" not in briefing_draft |
| DU-BG-03 | Review reference in draft | review_reference="REF-AABBCCDD" | "REF-AABBCCDD" in briefing_draft |
| DU-BG-04 | Citations populated | gap_analysis with citations | briefing_citations non-empty, record IDs present |
| DU-BG-05 | Incorporates feedback on revision | revision_count=1, rejection_feedback set | feedback text in draft, "Revision Notice" present |
| DU-BG-06 | No revision section on first draft | revision_count=0 | "Revision Notice" absent |

### HitlReviewGateNode

| DU-ID | Test | State Input | Expected Output |
|-------|------|-------------|-----------------|
| DU-HG-01 | Approve path | approval_decision="approve" | status=SUCCESS, approval_timestamp set |
| DU-HG-02 | Reject path | approval_decision="reject", rejection_feedback set | status=RETRY, revision_count incremented |
| DU-HG-03 | Reject increments from N | revision_count=1, decision="reject" | revision_count=2 |
| DU-HG-04 | hitl_allowed=False skips interrupt | hitl_allowed=False, approval_decision="" | no GraphInterrupt, status=AWAITING_HUMAN |
| DU-HG-05 | hitl_allowed=True raises interrupt | hitl_allowed=True, approval_decision="" | GraphInterrupt raised |
| DU-HG-06 | No implicit approval | hitl_allowed=False, no decision | status != SUCCESS |

---

## Boundary-Layer Tests (BL series)

| BL-ID | Test | Description |
|-------|------|-------------|
| BL-01 | EDU domain: multi-teacher cohort | cohort_filter with 3 teachers → evidence_records cover all refs |
| BL-02 | EDU domain: all requirements met | earned_hours >= required for all categories → gap_analysis all is_gap=False |
| BL-03 | EDU domain: all red urgency | deadlines all within 30 days → all urgency=red, cohort_roll_up reflects |
| BL-04 | EDU domain: revision loop | reject → briefing regeneration → approve → SUCCESS |
| BL-05 | EDU domain: max cohort size | cohort_filter producing 50 teachers stays within max_cohort_size=50 |
| BL-06 | EDU domain: empty evidence graceful | evidence_records=[] → gap_analysis all shortfall=required, no crash |
| BL-07 | PreProcess rejects empty institution_config | institution_config={} → status=ERROR |
| BL-08 | PreProcess rejects invalid date range | start > end → status=ERROR |

---

## Test Coverage Requirements

| Component | Min Line Coverage |
|-----------|------------------|
| src/nodes/pre_process_node.py | 90% |
| src/nodes/post_process_node.py | 90% |
| src/nodes/record_retrieval_node.py | 80% |
| src/nodes/requirements_mapping_node.py | 85% |
| src/nodes/gap_deadline_analysis_node.py | 85% |
| src/nodes/briefing_generation_node.py | 80% |
| src/nodes/hitl_review_gate_node.py | 85% |
| src/schemas/state.py | 100% (import only) |
| Overall | 85% |
