# Submission checklist

Official rules: https://github.com/microsoft/insidethegamehackathon/blob/main/OFFICIAL%20RULES.md (checked 18 Oct 2026).
Key dates (Pacific Time): registration closes **20 Oct 2026, 12:00 pm**; submissions close **27 Oct 2026, 11:59 pm**; judging 27 Oct – 10 Nov 2026 (the demo must stay free to test until judging ends).

Legend: ✅ done and verified · ⚠️ done but not verified in the build environment · ⬜ developer action required

## Product and engineering

| Item | Status | Evidence |
|---|---|---|
| Synthetic Match Lab, 6 scenarios, ground truth, seeds, CSV/JSON export | ✅ | `matchpulse/synth.py`, `data/samples/`, `/api/dataset` |
| Schema validation, normalization, failure cases | ✅ | `tests/test_core.py` |
| Deterministic analytics with documented formulas | ✅ | `docs/METRICS.md` |
| Explainable match-shift detection | ✅ | `matchpulse/shifts.py` |
| Multi-agent workflow on Microsoft Agent Framework | ✅ | `matchpulse/workflow.py`, trace in UI, `tests/test_workflow_api.py` |
| Microsoft Foundry narrative agent | ⚠️ | Code path tested with scripted Agent Framework clients; **no live Foundry call made yet** |
| Casual / analyst / broadcast, EN + ID | ✅ | UI, `reports/EVALUATION.md` personalization section |
| Evidence & Quality Reviewer, regeneration, fallback | ✅ | Fault-injection mode, tests |
| Live replay mode (minute-by-minute, no future data) | ✅ | `tests/test_live.py`, live section of `reports/EVALUATION.md` |
| Dashboard | ✅ | Local run + screenshots (desktop and 390 px mobile) |
| Tests | ✅ | 66 passed |
| Reviewer red-team (8 fault types + paraphrase false alarms) | ✅ | `matchpulse/redteam.py`, `tests/test_redteam.py`, red-team section of `reports/EVALUATION.md` |
| Evaluation results | ✅ | `reports/EVALUATION.md` |
| Dockerfile | ✅ | Built in Azure by `az containerapp up --source .` and running |
| Azure Container Apps deployment | ✅ | https://matchpulse.politefield-ec1b9bf6.southeastasia.azurecontainerapps.io — deployed 20 Oct 2026 via Cloud Shell (southeastasia, rg-matchpulse, min/max replicas 1/3); `/api/health` returned `status: ok` |

## Official submission fields

| Field | Status | Where |
|---|---|---|
| Registered before 20 Oct 2026 12:00 pm PT | ✅ | Registered on Innovation Studio |
| Project page created on Innovation Studio | ⬜ | "Create project" in the hackathon site |
| Project name + description (what, Microsoft tech, problem) | ✅ draft | `docs/PITCH.md` |
| Public GitHub repository URL | ✅ | https://github.com/Albategnius/matchpulse |
| Working demo link (free to test until 10 Nov) | ✅ | https://matchpulse.politefield-ec1b9bf6.southeastasia.azurecontainerapps.io |
| Demo video < 2 min, public on YouTube/Vimeo, no copyrighted music | ⬜ | Script: `docs/DEMO_SCRIPT.md` |
| Materials in English | ✅ | All docs |

## Eligibility and IP

| Check | Status |
|---|---|
| Age of majority where you live; not a resident of an excluded country/region | ⬜ confirm |
| Not a Microsoft/Premier League employee or family/household member | ⬜ confirm |
| Built during the hackathon period, original | ✅ written from scratch starting 18 Oct 2026 (with AI coding assistance) |
| Synthetic data only; no Premier League marks or data | ✅ |
| Open-source licenses respected | ✅ MIT-compatible dependencies |
| No secrets in repo | ✅ no keys exist; `.env` ignored — re-check with `git grep -i key` before pushing |
| Not previously awarded in another contest | ⬜ confirm |

## Suggested category

Best Multi-Agent System (primary) — the reviewer-controlled regeneration loop is the strongest technical story. If Foundry is connected and verified, Best Use of Microsoft Foundry Project is also a fit.
