# healthcare-eligibility-mcp

AI-powered Healthcare Eligibility MCP Server using X12 270/271, Databricks, Python, and MCP tools.

The server exposes healthcare eligibility workflows (patient coverage lookup, X12 270 request generation, 271 response parsing, eligibility rules and denial intelligence) as [Model Context Protocol](https://modelcontextprotocol.io) tools, resources and prompts. An AI client such as Claude or MCP Inspector can then answer questions like:

> "Check John Smith's eligibility and tell me why he was rejected and what the RCM team should do."

The agent uses the MCP tools to retrieve the data, analyze the 271, run the rules, and return a structured RCM explanation.

> **Note:** All patient and payer data in this project is synthetic. No real PHI is used.

## Architecture

```
                 HEALTHCARE ELIGIBILITY MCP
                            │
                            ▼
                    ┌───────────────┐
                    │  MCP Client   │
                    │  Claude /     │
                    │  MCP Inspector│
                    └───────┬───────┘
                            │
                       MCP Protocol
                            │
                            ▼
                 ┌──────────────────────┐
                 │   MCP SERVER         │
                 │   Python             │
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
                                  Gold Delta
```

### Eligibility data flow

```
Patient / Payer / Coverage (SQLite)
        │
        ▼
  270 Generator ──► Mock Payer API ──► 271 Response
                                           │
                                           ▼
                                      271 Parser
                                           │
                                           ▼
                                   Eligibility Rules
                                  (COB · PCP · Benefits)
                                           │
                                           ▼
                                  Denial Intelligence
                                           │
                                           ▼
                           Databricks (Bronze → Silver → Gold)
                                           │
                                           ▼
                                 LLM / AI Agent → RCM action
```

## Planned MCP tools

| Tool | Purpose |
|---|---|
| `check_patient_eligibility()` | End-to-end eligibility check for a patient |
| `validate_eligibility_request()` | Validate required fields before building a 270 |
| `generate_270()` | Build an X12 270 eligibility inquiry |
| `submit_eligibility()` | Send the 270 to the (mock) payer |
| `parse_271()` | Parse an X12 271 eligibility response |
| `get_patient_coverage()` | Look up a patient's coverage records |
| `check_benefits()` | Summarize benefits from the 271 |
| `detect_cob()` | Detect coordination-of-benefits issues |
| `validate_pcp()` | Check PCP assignment requirements |
| `identify_denial()` | Identify the eligibility denial reason |
| `recommend_rcm_action()` | Recommend the next RCM team action |

## Current server

[server.py](server.py) currently exposes a hello-world foundation:

| Type | Name | Description |
|---|---|---|
| Tool | `hello_eligibility(name)` | Test tool that confirms the server is working |
| Resource | `eligibility://status` | Current status of the eligibility platform |
| Prompt | `eligibility_analysis(patient_id)` | Eligibility-analysis checklist prompt |

## Tech stack

- Python 3.12 (3.10+ required)
- [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk) v2 (`MCPServer`)
- Pydantic, python-dotenv, pytest
- SQLite for local development
- Databricks Free Edition (Delta Lake, medallion architecture)
- Docker and GitHub Actions (planned)

## Project structure

```
healthcare-eligibility-mcp/
├── src/              # Eligibility domain code (built progressively)
│   └── __init__.py
├── tests/            # pytest tests
├── data/             # Synthetic data and the SQLite database
├── server.py         # MCP server entry point
├── requirements.txt  # Python dependencies
├── .env              # Local secrets (not committed)
├── .gitignore
└── README.md
```

## Getting started

### Prerequisites

- Python 3.10 or newer (3.11 or 3.12 recommended)
- Node.js, which MCP Inspector (`mcp dev`) needs

### Setup

```bash
git clone https://github.com/kiranraou/healthcare-eligibility-mcp.git
cd healthcare-eligibility-mcp

python3.12 -m venv .venv
source .venv/bin/activate

python -m pip install --upgrade pip
pip install -r requirements.txt
```

In VS Code, run **Python: Select Interpreter** and choose `.venv/bin/python`.

### Run the server

```bash
python server.py
```

The server talks over stdio, so it waits silently for an MCP client to connect. Stop it with `Ctrl+C`.

### Test with MCP Inspector

```bash
mcp dev server.py
```

Call `hello_eligibility` with `{"name": "Kiran"}`. Expected result:

```
Healthcare Eligibility MCP is working, Kiran!
```

## Roadmap

| Step | Milestone | Status |
|---|---|---|
| 01 | Create GitHub repo | ✅ Done |
| 02 | Create Python virtual environment | ✅ Done |
| 03 | Install MCP SDK | ✅ Done |
| 04 | Build Hello-World MCP server | ✅ Done |
| 05 | Connect MCP Inspector | ⏳ Next |
| 06 | Create Tools | ⬜ |
| 07 | Create Resources | ⬜ |
| 08 | Create Prompts | ⬜ |
| 09 | Create SQLite healthcare database | ⬜ |
| 10 | Build synthetic patients/payers | ⬜ |
| 11 | Build 270 generator | ⬜ |
| 12 | Build mock payer | ⬜ |
| 13 | Build 271 parser | ⬜ |
| 14 | Build eligibility rules | ⬜ |
| 15 | Build denial intelligence | ⬜ |
| 16 | Connect Databricks | ⬜ |
| 17 | Bronze → Silver → Gold | ⬜ |
| 18 | Add LLM | ⬜ |
| 19 | Build AI agent | ⬜ |
| 20 | MCP + AI end-to-end | ⬜ |
| 21 | Tests | ⬜ |
| 22 | Docker | ⬜ |
| 23 | GitHub Actions | ⬜ |
| 24 | Final architecture + README | ⬜ |
