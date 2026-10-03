# healthcare-eligibility-mcp

[![CI](https://github.com/kiranraou/healthcare-eligibility-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/kiranraou/healthcare-eligibility-mcp/actions/workflows/ci.yml)

AI-powered Healthcare Eligibility MCP Server using X12 270/271, Databricks, Python, and MCP tools.

The server exposes the full insurance eligibility workflow as [Model Context Protocol](https://modelcontextprotocol.io) tools, resources and prompts. It covers patient lookup, X12 270 generation, payer submission, 271 parsing, eligibility rules, denial intelligence and analytics. An AI agent built on Claude uses those tools to answer questions like:

> "Check John Smith's eligibility and tell me why he was rejected and what the RCM team should do."

The agent looks the patient up, runs the 270/271 cycle, applies the rules, and returns a structured RCM explanation. That explanation covers the root cause (payer AAA reject code), the likely denial category with CARC codes, and prioritized actions with owners.

> **Note:** All patient, payer and provider data is synthetic. No real PHI is used.

## Architecture

```
                 HEALTHCARE ELIGIBILITY MCP
                            │
                            ▼
                    ┌───────────────┐
                    │  MCP Client   │
                    │  Claude /     │
                    │  AI Agent /   │
                    │  MCP Inspector│
                    └───────┬───────┘
                            │
                MCP Protocol (stdio or HTTP)
                            │
                            ▼
                 ┌──────────────────────┐
                 │   MCP SERVER         │
                 │   server.py          │
                 └──────────┬───────────┘
                            │
          ┌─────────────────┼──────────────────┐
          │                 │                  │
          ▼                 ▼                  ▼
    Eligibility         270/271             RCM Rules
    Tools               Tools               Engine
          │                 │                  │
          └─────────────────┼──────────────────┘
                            │
                 ┌──────────┴──────────┐
                 ▼                     ▼
             SQLite                Databricks
             Local DB              Free Edition
                 │                     │
                 ▼                     ▼
           Development           Bronze/Silver/
           + local medallion      Gold Delta
```

### Eligibility data flow

```
Registration data (SQLite: patients, coverages)
        │
        ▼
 validate_request ──► 270 generator ──► Mock payer ──► 271 response
                                       (payer enrollment            │
                                        file, AAA rejects)          ▼
                                                               271 parser
                                                                    │
                                                                    ▼
                                                          Eligibility rules
                                                   (coverage · COB · PCP · referral
                                                     · member ID · benefits)
                                                                    │
                                                                    ▼
                                                         Denial intelligence
                                                     (category · CARC · RCM actions)
                                                                    │
                          ┌─────────────────────────────────────────┤
                          ▼                                         ▼
          eligibility_transactions ──► Bronze ──► Silver ──► Gold    AI agent (Claude)
                                     (raw X12) (parsed) (KPIs)       → RCM explanation
```

The mock payer keeps its own enrollment file, separate from the provider's registration data. Like a real payer, it only sees what's in the 270, so registration errors surface as genuine AAA rejections.

## Synthetic scenarios

| Patient | Name | Payer | Scenario | Status | Denial category (CARC) |
|---|---|---|---|---|---|
| P001 | John Smith | Aetna | Member ID keyed wrong at registration | REJECTED (AAA 72) | INVALID_MEMBER_ID (31) |
| P002 | Maria Garcia | BCBS | Clean, active PPO | ELIGIBLE | none |
| P003 | Robert Johnson | UHC | Plan terminated 2026-06-30 | INELIGIBLE | COVERAGE_TERMINATED (27) |
| P004 | Linda Williams | Cigna | Medicare is primary | ELIGIBLE_WITH_ISSUES | COORDINATION_OF_BENEFITS (22) |
| P005 | David Brown | UHC HMO | Wrong PCP on file, referral required | ELIGIBLE_WITH_ISSUES | PCP_NOT_ASSIGNED (242, 288) |
| P006 | Susan Miller | BCBS | Registered to the wrong payer | REJECTED (AAA 75) | SUBSCRIBER_NOT_FOUND (31) |
| P007 | James Davis | Aetna | DOB keyed wrong | REJECTED (AAA 71) | DOB_MISMATCH (31) |
| P008 | Patricia Wilson | Cigna HDHP | Deductible not met | ELIGIBLE | PATIENT_RESPONSIBILITY |
| P009 | Michael Moore | Humana | Payer system down | PAYER_UNAVAILABLE (AAA 42) | PAYER_UNAVAILABLE |

## MCP interface

### Tools

| Tool | Purpose |
|---|---|
| `find_patient(name)` | Search patients by name |
| `list_patients()` | All patients with their primary payer |
| `get_patient_coverage(patient_id)` | Demographics and registered coverage |
| `validate_eligibility_request(patient_id, date_of_service?)` | Pre-submission checks: required fields, member ID, NPI check digit, dates |
| `generate_270(patient_id, date_of_service?, service_type_code?)` | Build an X12 005010X279A1 270 |
| `submit_eligibility(x12_270)` | Send a 270 to the mock payer, get the raw 271 |
| `parse_271(x12_271)` | Parse a 271 into JSON (subscriber, dates, AAA rejections, EB benefits) |
| `check_patient_eligibility(patient_id, date_of_service?, rendering_npi?, include_x12?)` | Full workflow, recorded for analytics |
| `check_benefits(patient_id)` | Copay, deductible, coinsurance, out-of-pocket |
| `detect_cob(patient_id)` | Coordination-of-benefits problems |
| `validate_pcp(patient_id, rendering_npi?)` | PCP assignment and referral requirements |
| `identify_denial(patient_id)` | Denial category, CARC codes, root cause |
| `recommend_rcm_action(patient_id)` | Prioritized RCM actions with owners |
| `run_medallion_pipeline(backend?)` | Bronze → Silver → Gold (SQLite or Databricks) |
| `get_eligibility_analytics(backend?)` | Gold tables: payer performance, denial categories, latest status |
| `hello_eligibility(name)` | Connectivity test |

### Resources

| URI | Content |
|---|---|
| `eligibility://status` | Platform status, counts, offline payers |
| `eligibility://payers` | Payers with EDI IDs and online status |
| `eligibility://patients/{patient_id}` | Patient record and coverage |
| `eligibility://reference/x12-codes` | EB01, service type, AAA reject and follow-up code tables |
| `eligibility://reference/denial-rules` | Finding → denial category, CARC, actions |

### Prompts

| Prompt | Use |
|---|---|
| `eligibility_analysis(patient_id)` | 10-point eligibility review |
| `denial_root_cause(patient_name)` | Why a check failed and what RCM should do |
| `daily_rcm_worklist()` | Worklist across all patients, then analytics |

## Project structure

```
healthcare-eligibility-mcp/
├── server.py                 # MCP server (tools, resources, prompts)
├── app.py                    # Streamlit UI
├── src/
│   ├── config.py             # Settings from environment / .env
│   ├── models.py             # Pydantic domain models
│   ├── db.py                 # SQLite schema and queries
│   ├── seed.py               # Synthetic patients, payers, coverage, payer enrollment
│   ├── service.py            # Eligibility workflow orchestration + request validation
│   ├── rules.py              # Eligibility rules engine (coverage, COB, PCP, referral, benefits)
│   ├── denials.py            # Denial intelligence (category, CARC, RCM actions)
│   ├── llm.py                # Claude explanation of a result (single call)
│   ├── agent.py              # Claude agent using this MCP server's tools
│   ├── x12/
│   │   ├── common.py         # Envelope, separators, X12 code tables
│   │   ├── x270.py           # 270 generator and reader
│   │   └── x271.py           # 271 builder and parser
│   ├── payer/
│   │   └── mock_payer.py     # Mock payer answering 270s from its enrollment file
│   └── pipeline/
│       ├── sinks.py          # SQLite and Databricks SQL backends
│       └── medallion.py      # Bronze → Silver → Gold
├── tests/                    # pytest suite (51 tests)
├── scripts/smoke_test.py     # HTTP smoke test used by CI
├── data/                     # Local SQLite database (generated, git-ignored)
├── Dockerfile
├── .github/workflows/ci.yml
├── requirements.txt
├── pyproject.toml
└── .env.example
```

## Getting started

### Prerequisites

- Python 3.10 or newer (3.12 recommended)
- Node.js, which MCP Inspector (`mcp dev`) needs
- Optional: an Anthropic API key (AI agent), Databricks Free Edition (Delta tables), Docker

### Setup

```bash
git clone https://github.com/kiranraou/healthcare-eligibility-mcp.git
cd healthcare-eligibility-mcp

python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env        # then fill in what you need
python -m src.seed          # optional: the server seeds on first start
```

In VS Code, run **Python: Select Interpreter** and choose `.venv/bin/python`.

### Run the MCP server

```bash
python server.py                                  # stdio (for Claude Desktop, agents)
MCP_TRANSPORT=streamable-http python server.py    # HTTP at http://127.0.0.1:8000/mcp
mcp dev server.py                                 # MCP Inspector in the browser
```

In MCP Inspector, call `check_patient_eligibility` with `{"patient_id": "P001"}`.

### Use it from Claude Desktop

Add this to `claude_desktop_config.json`, using your own absolute paths:

```json
{
  "mcpServers": {
    "healthcare-eligibility": {
      "command": "/ABSOLUTE/PATH/healthcare-eligibility-mcp/.venv/bin/python",
      "args": ["/ABSOLUTE/PATH/healthcare-eligibility-mcp/server.py"]
    }
  }
}
```

### Streamlit app

```bash
streamlit run app.py        # http://localhost:8501
```

| Tab | What it shows |
|---|---|
| Eligibility check | Input (patient, registration coverage, validation, X12 270) next to output (X12 271, AAA rejections, benefits), plus KPIs, rule findings, denial analysis and RCM actions. "Explain with Claude" adds a plain-language summary. |
| Worklist | Every patient checked and ranked by denial risk, with the next action and owner |
| Analytics | Runs Bronze → Silver → Gold on SQLite or Databricks; charts checks by status and failure rate per payer |
| AI assistant | Ask the Claude agent a question; shows the MCP tools it called and its answer |

### Ask the AI agent

Requires `ANTHROPIC_API_KEY` in `.env`. The agent starts `server.py` as an MCP server and gives Claude all of its tools.

```bash
python -m src.agent --show-tools "Check John Smith's eligibility and tell me why he was rejected and what the RCM team should do."
```

If your key is not scoped to a workspace, also set `ANTHROPIC_WORKSPACE_ID`. The default model is `claude-opus-5-5`; override it with `ANTHROPIC_MODEL`. Requests use the Claude API's server-side refusal fallback (`fallbacks: "default"`).

### Run the Bronze → Silver → Gold pipeline

```bash
python -m src.pipeline.medallion --backend sqlite       # local tables in data/eligibility.db
python -m src.pipeline.medallion --backend databricks   # Delta tables in Databricks
```

| Layer | Table | Content |
|---|---|---|
| Bronze | `bronze_x12_raw` | Raw 270 and 271 payloads, append-only, incremental |
| Silver | `silver_eligibility` | One parsed, typed row per 271 |
| Gold | `gold_payer_performance` | Checks and failure rate by payer |
| Gold | `gold_denial_categories` | Denial categories by occurrences and patients |
| Gold | `gold_patient_latest_status` | Latest eligibility status per patient |

**Databricks Free Edition setup:**
1. Sign up at databricks.com/learn/free-edition.
2. Open **SQL Warehouses**, choose your warehouse, then **Connection details**. Copy the server hostname and HTTP path.
3. Go to **User settings → Developer → Access tokens** and generate a token.
4. Put all three values in `.env` (`DATABRICKS_SERVER_HOSTNAME`, `DATABRICKS_HTTP_PATH`, `DATABRICKS_TOKEN`). Tables are created in `workspace.eligibility` by default.

When Databricks is configured, `run_medallion_pipeline` uses it by default.

### Docker

```bash
docker build -t healthcare-eligibility-mcp .
docker run -p 8000:8000 healthcare-eligibility-mcp
python scripts/smoke_test.py http://localhost:8000/mcp
```

### Tests

```bash
python -m pytest -v
```

The suite covers X12 structure and round trips, all nine scenarios, rules, denial analysis, the pipeline (including the SQL sent to Databricks), every MCP tool, resource and prompt, and the AI agent. The agent tests script Claude's replies with a mock HTTP transport while the tool runner, MCP server and workflow run for real.

GitHub Actions runs the tests on every push and pull request, then builds the Docker image and smoke-tests the container over HTTP.

## Roadmap

| Step | Milestone | Status |
|---|---|---|
| 01 | Create GitHub repo | ✅ |
| 02 | Create Python virtual environment | ✅ |
| 03 | Install MCP SDK | ✅ |
| 04 | Build Hello-World MCP server | ✅ |
| 05 | Connect MCP Inspector | ✅ |
| 06 | Create Tools | ✅ |
| 07 | Create Resources | ✅ |
| 08 | Create Prompts | ✅ |
| 09 | Create SQLite healthcare database | ✅ |
| 10 | Build synthetic patients/payers | ✅ |
| 11 | Build 270 generator | ✅ |
| 12 | Build mock payer | ✅ |
| 13 | Build 271 parser | ✅ |
| 14 | Build eligibility rules | ✅ |
| 15 | Build denial intelligence | ✅ |
| 16 | Connect Databricks | ✅ |
| 17 | Bronze → Silver → Gold | ✅ |
| 18 | Add LLM | ✅ |
| 19 | Build AI agent | ✅ |
| 20 | MCP + AI end-to-end | ✅ |
| 21 | Tests | ✅ |
| 22 | Docker | ✅ |
| 23 | GitHub Actions | ✅ |
| 24 | Final architecture + README | ✅ |

## Tech stack

Python 3.12 · Streamlit · MCP Python SDK v2 (`MCPServer`) · Anthropic Python SDK (Claude tool runner + MCP helpers) · Pydantic · SQLite · Databricks SQL Connector (Delta Lake) · pytest · Docker · GitHub Actions
