# OptiSchedule - Generalized Timetable Engine ⚙️

OptiSchedule is an AI-deterministic, constraint-based weekly timetable scheduler designed for schools and institutions. Instead of relying on heuristic guessing, OptiSchedule treats roster generation as a **Constraint Satisfaction Problem (CSP)**, utilizing Google's OR-Tools CP-SAT solver to guarantee a **zero hard-constraint violation rate**.

The platform is designed to be completely domain-agnostic and multi-tenant. No teacher, class, subject, or organization identity is ever hardcoded; all configuration, rules, and constraints are ingested dynamically.

---

## 🚀 Key Highlights

*   **Deterministic CP-SAT Solver:** Powered by Google OR-Tools to mathematically solve for conflict-free weekly grids.
*   **Dual Validation Pipeline:**
    *   *Pre-solve validator* checks mathematical bounding limits (e.g. demand exceeding physical slots) and stops infeasible solving immediately.
    *   *Post-solve validator* independently inspects the raw output to guarantee zero hard-constraint violations before persisting.
*   **Natural Language Constraint Parser:** Reads and interprets complex constraint instructions directly from spreadsheet cells (e.g., *"Half-Day (Time: 07:30 AM - 10:30 AM) Monday - Saturday OR Maximum 3 Lectures Daily"*).
*   **Structure-Locked Spreadsheet Ingestion:** Custom Excel worksheets are protected using workbook-level structure locking to prevent structure modifications, while data ranges remain open for inputs.
*   **Interactive Glassmorphism SPA:** Features a beautiful modern dashboard with direct view toggles (Class view vs Teacher view) and real-time manual overrides validated instantly in the backend.

---

## 🛠️ Technology Stack

*   **Backend Framework:** Python 3.12, FastAPI (async/lifespan lifespan management)
*   **Solver Library:** Google OR-Tools (`ortools.sat.python.cp_model`)
*   **Database & ORM:** PostgreSQL, SQLAlchemy 2.0 (asyncio), asyncpg
*   **Spreadsheet Processor:** openpyxl
*   **Live updates:** WebSockets
*   **Testing Suite:** pytest, Hypothesis (property-based randomized tests)
*   **Deployment:** Docker, Docker Compose

---

## 📐 CP-SAT Solver Formulation

### 1. Variables
*   `x[teacher, subject, class, day, period]` — Binary decision variable (1 if assigned, 0 otherwise). Created only for valid day/period cells within the teacher's availability window to optimize search space.

### 2. Hard Constraints (Strictly Enforced)
*   **Resource Double Booking:** A teacher can be assigned to at most one class in a given day and period.
*   **Group Double Booking:** A class can have at most one teacher/subject assigned in a given day and period.
*   **Exact Weekly Allocation:** The sum of periods assigned for any (teacher, subject, class) matches the required weekly count exactly.
*   **Teacher Availability:** Assigns are permitted only during teacher-allowed days/periods.
*   **Maximum Daily Load:** The count of lessons assigned to a teacher in a single day does not exceed their daily max capacity.

### 3. Soft Constraints (Optimized)
*   **Class Teacher Placement:** If a teacher is the class teacher of Class X, the solver prioritizes assigning their lessons to **Period 1** of Class X across all working days (Mon-Sat).
*   **Teacher Load Balancing:** Minimizes the deviation of daily lessons per teacher from their average workload, ensuring an even distribution of classes throughout the week.

---

## 📦 Setting Up Locally

Ensure you have [Docker](https://www.docker.com/) and [Docker Compose](https://docs.docker.com/compose/) installed.

1. Clone the repository and navigate to the project directory:
   ```bash
   cd timetable-engine
   ```
2. Build and start the services:
   ```bash
   docker-compose up -d --build
   ```
3. Open your browser and navigate to:
   ```
   http://localhost:8000
   ```
4. Run integration and solver tests:
   ```bash
   docker exec -e PYTHONPATH=. timetable-engine-web-1 pytest
   ```
"# Time-Table-Engine" 
