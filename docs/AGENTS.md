# Agent workflow

Implemented in `matchpulse/workflow.py` with **Microsoft Agent Framework** (`agent-framework-core` 1.22): `WorkflowBuilder`, `Executor`, `@handler`, `WorkflowContext`. Messages are routed by type, so each agent only receives the schema it is written for.

| Agent (executor id) | Input message | Output message | Responsibility |
|---|---|---|---|
| Event Analytics Agent (`event_analytics_agent`) | `MatchRequest` | `AnalyticsPacket` or error output | Validate rows, normalize, compute deterministic metrics. Never invents statistics. |
| Tactical Insight Agent (`tactical_insight_agent`) | `AnalyticsPacket` | `InsightPacket` | Detect match shifts vs. baselines; attach metric deltas, evidence events, strength, limitations; build the evidence registry. |
| Narrative & Personalization Agent (`narrative_agent`) | `InsightPacket`, `RegenerateRequest` | `DraftPacket` | Render casual / analyst / broadcast narratives in EN or ID. If Foundry is configured, an Agent Framework `Agent` with `FoundryChatClient` rewords rewritable claims (25 s timeout, strict JSON parsing). |
| Evidence & Quality Reviewer (`evidence_quality_reviewer`) | `DraftPacket` | `RegenerateRequest` or final output | Verify each claim; remove unsupported ones; reject drafts with a failed headline or >30% unsupported claims; request one regeneration with feedback; otherwise fall back to the verified template. |

Edges: analytics → insight → narrative → reviewer, plus reviewer → narrative (regeneration). `max_regenerations = 1`, workflow `max_iterations = 20`, whole-run timeout 90 s.

## What the reviewer checks

1. Every cited evidence id exists in the registry.
2. Every number in the sentence matches (after rounding) a value of the sentence's cited evidence. Numbers without citations fail.
3. No causal language (`caused`, `because of`, `led to`, `due to`, `menyebabkan`, `karena`, `akibat`…). Required hedges such as "not proven to be caused by" are allowed.
4. No team names outside this match.

## Example trace (fault-injection mode, high press seed 4)

```
1 Event Analytics Agent          ok          1675 valid events; 18 windows of 5 min; score 1-1
2 Tactical Insight Agent         ok          2 moment(s): M1 high_press AWAY 20-35', M2 high_press AWAY 65-75'
3 Narrative & Personalization    ok          3 narratives in 'id' via simulated-faulty-model
4 Evidence & Quality Reviewer    regenerate  rejected [analyst, broadcast, casual]
5 Narrative & Personalization    ok          regeneration attempt 2 using reviewer feedback
6 Evidence & Quality Reviewer    ok          checked 23 claims, removed 0; fallbacks: none
```

## What is and is not "agentic" here

The two analytic agents are deterministic on purpose: statistics must be reproducible and auditable. The Narrative agent is LLM-backed when Foundry is configured. The reviewer is a deterministic verifier that controls the loop. The collaboration is real orchestration (typed handoffs, a feedback loop, bounded retries, fallbacks) rather than four LLMs chatting — a deliberate design choice for factual reliability.

## Tests

`tests/test_workflow_api.py` drives the real Agent Framework `Agent` with scripted `BaseChatClient` subclasses (`tests/fakes.py`) to cover: faithful output, hallucinate-then-fix, persistent hallucination, invalid JSON, service error and timeout.
