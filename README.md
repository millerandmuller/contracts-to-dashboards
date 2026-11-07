# Role contracts that generate dashboards

Since AI Hackathon 2026, Bayer challenge "Build Less, Build Better: AI for Governed Analytics".

A vague request ("a monthly supply-risk dashboard by medicine and country") becomes a **versioned role contract**.
Before anything is built, the catalog is searched: the request arrived 9 times, 6 existing assets already carry 3 of the 4 numbers, 2 of them belong to inactive owners.
From one contract, **two role views** are drawn (they never count as assets). A threshold crossing opens a **handover** (open / acknowledged / closed) that only the receiving role can acknowledge. A **preservation test** blocks a consolidation that would silently flip a decision. Every change lands in an append-only **audit log**. The contract exports as a **Power BI project (PBIP)**. A role the catalog cannot serve gets an honest refusal that names the gap.

## Run

```
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn ctd.app:app --port 8800      # open http://localhost:8800
.venv/bin/python -m pytest -q tests            # end-to-end tests
```
Works offline. With `ANTHROPIC_API_KEY` set, three short texts come from the model (`CTD_MODEL`, default `claude-sonnet-5-5`, 8 s timeout). Without a key, or on any failure, a prepared text is shown with a visible `cached` badge. `CTD_OFFLINE=1` forces that.

## Architecture

| Layer | Code | Model? |
|---|---|---|
| Catalog, request clustering, owner check, lexical retrieval (every claim cites sheet + record IDs) | `ctd/catalog.py` | no |
| Interview: 3 fixed questions, wording from `data/role_ontology.yaml` | `ctd/interview.py` | no (examples come from the file) |
| Contract proposal, validation (owner active, official KPI, cadence covered by data, evidence cited), versions, diff | `ctd/builder.py`, `ctd/contracts.py` | no |
| Views: one dense page per contract, server-side SVG sparklines | `ctd/render.py`, `ctd/evaluate.py` | no |
| Handovers with role rights and computed overdue | `ctd/handovers.py` | no |
| Preservation test and human decision | `ctd/preserve.py` | no |
| Honest refusal (gap found by code) | `ctd/refuse.py` | wording only |
| PBIP export, deterministic (same contract, same hash) | `ctd/pbip.py` | no |
| Short explanatory texts | `ctd/gateway.py` | yes, with fallback |
| Interview reading: proposes contract fields from the answers; code checks every field; the contract is built from the checked result | `ctd/reader.py` | yes, with fixture fallback |

The model never picks a number, a threshold, an owner or a state. On the interview page it proposes contract fields from the answers and quotes the words each one comes from; code parses the same answers, shows every disagreement by name and builds the contract from the parsed result. Unknown numbers or roles from the model are discarded. `CTD_VIEW=v1` draws the earlier role view.

## Governance controls
Retrieval finds, never decides · owner must be active · one contract per responsibility, neighbours are edges · views are configuration, not assets · only the receiving role acknowledges · closing needs a reason · overdue is computed and never green · missing data is a visible gap, never interpolated · audit log is append-only (database triggers) · every number carries a footnote with sheet and record.

## What is real and what is curated
| Part | Status |
|---|---|
| Data pack (24 sheets), request cluster, owner states, series, thresholds, handover trigger | `[ECHT]` synthetic data from the organizer |
| `data/role_ontology.yaml` (28 roles, examples) | `[KURATIERT]`, no real people |
| Prepared interview answers, cached model texts | `[KURATIERT]`, shown as "prepared" / "cached" |
| Two KPI formulas for the preservation test (`data/formulas.yaml`) | `[KURATIERT]`, synthetic; the pack has none. The test mechanics are real |
| Response window of 48 h, acknowledgement wording | `[KURATIERT]` |
| Roles in the demo are picked, not logged in | simplification; rights are still enforced on the server |
| PBIP folder | generated from the published PBIP/PBIR/TMDL structure, **not yet opened in Power BI Desktop** |

Data notes: months with no shipments (products not on a market) are shown as gaps, not zeros. The latest month for some pairs is a forecast in the pack and is drawn hollow.

## Enterprise BI integration
The contract is YAML; the export is a PBIP folder that can live in git next to a Fabric workspace. A real catalog connection replaces `ctd/catalog.py` (same interface); the role list would be checked against the real org chart.

## AI tools used
Claude (Anthropic) was used as a coding assistant to write and review this repository, and optionally at runtime for three short texts, as described above.

## License
MIT
