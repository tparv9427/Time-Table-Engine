# OptiSchedule — Constraint-Based Timetable Engine

A multi-tenant, data-driven weekly scheduling engine that treats roster generation as a **Constraint Satisfaction Problem (CSP)**, solved with Google OR-Tools CP-SAT to guarantee zero hard-constraint violations — not through heuristic guessing, but through mathematical proof.

Designed for schools, institutions, and any organization with staff-scheduling needs. No teacher, class, subject, or organization name is ever hardcoded — all configuration is ingested dynamically at runtime.

---

## Key Features

- **Deterministic CP-SAT Solver** — Powered by Google OR-Tools. Returns an optimal or provably-feasible schedule, or explicitly reports infeasibility. No random-seed retries.
- **Dual Validation Pipeline** — Pre-solve validator catches mathematical impossibilities before the solver runs. Post-solve validator independently re-checks every hard constraint on the raw output before persisting.
- **Multi-Tenant Architecture** — Every database table and API endpoint is scoped by `organization_id`. One deployment serves multiple organizations with fully isolated data.
- **Natural Language Constraint Parser** — Reads constraint descriptions directly from spreadsheet cells (e.g., *"Half-Day 07:30 AM - 10:30 AM, Monday - Saturday, Maximum 3 Lectures"*) and converts them to solver parameters.
- **Real-Time Manual Overrides** — Edit individual schedule slots through the API; each edit is re-validated against all constraints before committing.
- **Live Updates** — WebSocket broadcast notifies connected clients on schedule changes.

---

## Architecture

```
Input Template (Excel / JSON API)
        │
        ▼
Ingestion Layer ──────────► Parses into normalized Resource, Allocation,
        │                   Availability, and Exception records
        ▼
Pre-Solve Validator ──────► Checks mathematical feasibility:
        │                   demand vs. capacity, cross-sheet consistency
        ▼
CP-SAT Solver ────────────► Builds and solves the CSP model
        │                   Returns OPTIMAL / FEASIBLE / INFEASIBLE
        ▼
Post-Solve Validator ─────► Independent re-verification of every hard
        │                   constraint from raw output — rejects on
        │                   any violation
        ▼
PostgreSQL ───────────────► All tables scoped by organization_id
        │
        ▼
REST API + WebSocket ─────► 14 endpoints, org-scoped, live refresh
```

---

## CP-SAT Solver Formulation

### Decision Variables

`x[teacher, subject, class, day, period]` — Binary. Created only for day/period cells within the teacher's availability window to reduce model size.

### Hard Constraints (5 — all enforced as literal `model.Add()` calls)

| # | Constraint | Formulation |
|---|-----------|-------------|
| 1 | **Exact Weekly Count** | `Σ x[r,t,g,*,*] == required_count` for each allocation |
| 2 | **No Teacher Double-Booking** | `Σ x[r,*,*,d,s] ≤ 1` for each resource at each (day, slot) |
| 3 | **No Class Double-Booking** | `Σ x[*,*,g,d,s] ≤ 1` for each group at each (day, slot) |
| 4 | **Max Daily Load** | `Σ x[r,*,*,d,*] ≤ max_per_day` for each resource per day |
| 5 | **No Banned Consecutive Repeat** | `x[task,g,d,s] + x[task,g,d,s+1] ≤ 1` unless explicitly excepted |

### Soft Constraints (Objective Minimization)

- **Teacher Load Balancing** — Minimizes deviation of daily assignments from the even-distribution target per teacher.
- **Class Teacher Placement** — Prioritizes assigning a class teacher's lessons to Period 1 of their home class.

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.12, FastAPI (async) |
| Solver | Google OR-Tools CP-SAT (`ortools.sat.python.cp_model`) |
| Database | PostgreSQL 15, SQLAlchemy 2.0 (async), asyncpg |
| Spreadsheet Parsing | openpyxl |
| Live Updates | WebSockets (native FastAPI) |
| Testing | pytest, Hypothesis (property-based) |
| Deployment | Docker, Docker Compose |

---

## API Endpoints

All endpoints are prefixed with `/api` and scoped to `organization_id` for multi-tenancy.

| Method | Endpoint | Description |
|--------|---------|-------------|
| `POST` | `/organizations` | Create a new organization |
| `POST` | `/organizations/setup-wizard` | Seed organization + solve in one step |
| `POST` | `/organizations/upload-roster` | Upload Excel roster, validate, and solve |
| `GET` | `/organizations` | List all organizations |
| `POST` | `/organizations/{org_id}/template` | Upload/replace input template |
| `GET` | `/organizations/{org_id}/template/status` | Get template validation status |
| `POST` | `/organizations/{org_id}/schedule/generate` | Run solver and persist schedule |
| `GET` | `/organizations/{org_id}/schedule` | Get full schedule |
| `GET` | `/organizations/{org_id}/schedule/resource/{id}` | Get schedule for one teacher |
| `GET` | `/organizations/{org_id}/schedule/group/{id}` | Get schedule for one class |
| `POST` | `/organizations/{org_id}/schedule/edit` | Manual override (re-validated) |
| `GET` | `/organizations/{org_id}/meta` | Get org metadata (days, slots, resources, groups) |
| `GET` | `/organizations/{org_id}/logs` | Get solver run history |
| `WS` | `/organizations/{org_id}/ws` | Live schedule update stream |

---

## Getting Started

### Prerequisites

- [Docker](https://www.docker.com/) and [Docker Compose](https://docs.docker.com/compose/)

### Run

```bash
git clone https://github.com/<your-username>/timetable-engine.git
cd timetable-engine
docker-compose up -d --build
```

The application will be available at **http://localhost:8000**.

### Run Tests

```bash
docker exec -e PYTHONPATH=. timetable-engine-web-1 pytest -v
```

This runs the full test suite including:
- **Pre-validator unit tests** — Confirms mathematical impossibility detection.
- **Post-validator unit tests** — Confirms broken schedules are caught (double-booking, allocation mismatches).
- **Solver integration test** — Verifies the CP-SAT solver returns OPTIMAL/FEASIBLE for valid inputs.
- **Property-based tests** — 50 randomized synthetic organizations tested with Hypothesis, asserting zero violations on feasible solutions and confirmed over-constraint on infeasible ones.

---

## Project Structure

```
timetable-engine/
├── app/
│   ├── main.py                  # FastAPI application entry point
│   ├── api/
│   │   └── endpoints.py         # All REST + WebSocket endpoints
│   ├── core/
│   │   ├── config.py            # Environment configuration
│   │   ├── database.py          # Async SQLAlchemy engine
│   │   ├── solver.py            # CP-SAT model construction and solving
│   │   ├── pre_validator.py     # Pre-solve feasibility checks
│   │   └── post_validator.py    # Post-solve independent verification
│   ├── models/
│   │   ├── models.py            # SQLAlchemy ORM models (all org-scoped)
│   │   └── schemas.py           # Pydantic request/response schemas
│   └── services/
│       └── ingestion.py         # Excel template parser + constraint NLP
├── frontend/                    # Static SPA (HTML/CSS/JS)
├── tests/
│   ├── test_timetable.py        # Unit + integration tests
│   └── test_property.py         # Hypothesis property-based tests
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```

---

## License

MIT
