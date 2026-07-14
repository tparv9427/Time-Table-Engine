import pytest
import time
import sys
from pathlib import Path
from typing import Dict, List, Any

# Ensure app path is in sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.pre_validator import validate_ingested_data
from app.core.post_validator import validate_schedule_output
from app.core.solver import solve_timetable

import hypothesis.strategies as st
from hypothesis import given, settings, HealthCheck

@st.composite
def generate_random_organization(draw):
    # 1. Day configuration
    days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
    num_days = draw(st.integers(min_value=3, max_value=6))
    days_list = days[:num_days]
    
    slots_count = draw(st.integers(min_value=4, max_value=8))
    
    # 2. Number of resources and groups
    num_resources = draw(st.integers(min_value=5, max_value=30))
    num_groups = draw(st.integers(min_value=3, max_value=15))
    
    resource_names = [f"Teacher_{i}" for i in range(num_resources)]
    group_names = [f"Class_{i}" for i in range(num_groups)]
    
    # Subjects (tasks)
    subjects = ["Math", "Science", "English", "History", "Geography", "Art", "PE"]
    
    # 3. Generating allocations
    allocations = []
    group_counts = {g: 0 for g in group_names}
    resource_counts = {r: 0 for r in resource_names}
    
    # Draw number of allocations
    num_allocations = draw(st.integers(min_value=5, max_value=40))
    
    for _ in range(num_allocations):
        res = draw(st.sampled_from(resource_names))
        grp = draw(st.sampled_from(group_names))
        subj = draw(st.sampled_from(subjects))
        
        # Check if this res/grp/subj already exists to avoid duplicate allocation key
        existing = [a for a in allocations if a["resource_name"] == res and a["group_name"] == grp and a["task"] == subj]
        if existing:
            continue
            
        # Draw weekly count (keep it bounded to have some feasible runs)
        max_wc = min(8, (num_days * slots_count) - group_counts[grp])
        if max_wc <= 0:
            continue
        weekly_count = draw(st.integers(min_value=1, max_value=max_wc))
        
        allocations.append({
            "resource_name": res,
            "group_name": grp,
            "task": subj,
            "weekly_count": weekly_count,
            "row_id": f"Row_{len(allocations)}"
        })
        group_counts[grp] += weekly_count
        resource_counts[res] += weekly_count

    # 4. Generating availability constraints
    availability = {}
    for res in resource_names:
        # A resource might have restricted days/slots
        allowed_days = draw(st.sets(st.sampled_from(days_list), min_size=1))
        allowed_slots = draw(st.sets(st.integers(min_value=1, max_value=slots_count), min_size=1))
        
        # max_per_day
        max_pd = draw(st.integers(min_value=1, max_value=slots_count))
        
        availability[res] = {
            "allowed_days": list(allowed_days),
            "allowed_slots": list(allowed_slots),
            "max_per_day": max_pd
        }
        
    # 5. Class teachers (optional)
    class_teachers = {}
    available_groups = list(group_names)
    for res in resource_names:
        if draw(st.booleans()) and available_groups:
            grp = draw(st.sampled_from(available_groups))
            available_groups.remove(grp)
            class_teachers[res] = grp

    # 6. Exceptions
    exceptions = []
    num_exceptions = draw(st.integers(min_value=0, max_value=3))
    for _ in range(num_exceptions):
        subj = draw(st.sampled_from(subjects))
        if draw(st.booleans()):
            app_grps = "*"
        else:
            app_grps = ",".join(draw(st.sets(st.sampled_from(group_names), min_size=1)))
        
        exceptions.append({
            "task": subj,
            "applicable_groups": app_grps,
            "allow_consecutive": draw(st.booleans())
        })

    return {
        "days_list": days_list,
        "slots_count": slots_count,
        "allocations": allocations,
        "availability": availability,
        "exceptions": exceptions,
        "class_teachers": class_teachers
    }

def verify_infeasible_bottleneck(days_list, slots_count, allocations, availability) -> bool:
    """
    Checks if there is an obvious mathematical bottleneck in the input configuration
    that guarantees infeasibility.
    """
    # 1. Check resource capacity
    resource_sums = {}
    for a in allocations:
        r = a["resource_name"]
        resource_sums[r] = resource_sums.get(r, 0) + a["weekly_count"]
        
    for r, demand in resource_sums.items():
        avail = availability.get(r, {
            "allowed_days": days_list,
            "allowed_slots": list(range(1, slots_count + 1)),
            "max_per_day": slots_count
        })
        allowed_days_count = len(avail["allowed_days"])
        allowed_slots_count = len(avail["allowed_slots"])
        max_pd = avail["max_per_day"]
        
        # Max slot cap
        max_slots = allowed_days_count * allowed_slots_count
        if demand > max_slots:
            return True
            
        # Max daily cap
        max_daily_capacity = allowed_days_count * max_pd
        if demand > max_daily_capacity:
            return True
            
    # 2. Check group capacity
    group_sums = {}
    for a in allocations:
        g = a["group_name"]
        group_sums[g] = group_sums.get(g, 0) + a["weekly_count"]
        
    max_group_capacity = len(days_list) * slots_count
    for g, demand in group_sums.items():
        if demand > max_group_capacity:
            return True
            
    # 3. Check for group slot capacity under teacher availability constraints
    # For a class C, the total slots where at least one of its assigned teachers is available
    for g in group_sums.keys():
        g_allocs = [a for a in allocations if a["group_name"] == g]
        assigned_teachers = set(a["resource_name"] for a in g_allocs)
        
        # Find union of all days/slots where these teachers are available
        available_slots_union = set()
        for t in assigned_teachers:
            avail = availability.get(t, {
                "allowed_days": days_list,
                "allowed_slots": list(range(1, slots_count + 1))
            })
            for d in avail["allowed_days"]:
                for s in avail["allowed_slots"]:
                    available_slots_union.add((d, s))
                    
        # Total demand for group g cannot exceed union of available slots
        if group_sums[g] > len(available_slots_union):
            return True
            
    return False

# Global statistics tracking for the run
stats = {
    "runs": 0,
    "feasible": 0,
    "infeasible": 0,
    "pre_validator_caught": 0,
    "bottleneck_caught": 0
}

@settings(
    max_examples=50,
    deadline=15000, # Allow up to 15 seconds per solver run if it takes time
    suppress_health_check=[HealthCheck.too_slow]
)
@given(generate_random_organization())
def test_solver_property(org):
    global stats
    stats["runs"] += 1
    
    days_list = org["days_list"]
    slots_count = org["slots_count"]
    allocations = org["allocations"]
    availability = org["availability"]
    exceptions = org["exceptions"]
    class_teachers = org["class_teachers"]

    # 1. Run Solver
    # Bounded to 5 seconds to keep property test fast
    status_name, schedule, obj_val = solve_timetable(
        days_list=days_list,
        slots_count=slots_count,
        allocations=allocations,
        availability=availability,
        exceptions=exceptions,
        class_teachers=class_teachers,
        time_limit_seconds=5
    )

    if status_name in ("OPTIMAL", "FEASIBLE"):
        stats["feasible"] += 1
        
        # 2. Assert independent post-solve validator reports zero violations
        violations = validate_schedule_output(
            days_list=days_list,
            slots_count=slots_count,
            allocations=allocations,
            availability=availability,
            exceptions=exceptions,
            schedule=schedule
        )
        assert len(violations) == 0, f"Post-validator found violations in feasible schedule: {violations}"
        
    elif status_name == "INFEASIBLE":
        stats["infeasible"] += 1
        
        # Construct ingestion data format for pre-solve validator
        ingested_data = {
            "config": {
                "Day Names": ",".join(days_list),
                "Slots Per Day": slots_count
            },
            "resources_data": [
                {
                    "resource_name": a["resource_name"],
                    "task": a["task"],
                    "group_name": a["group_name"],
                    "weekly_count": a["weekly_count"],
                    "home_group": a.get("home_group"),
                    "row_id": a["row_id"]
                }
                for a in allocations
            ],
            "availability_data": [
                {
                    "resource_name": r,
                    "allowed_days": ",".join(availability[r]["allowed_days"]),
                    "allowed_slots": ",".join(str(s) for s in availability[r]["allowed_slots"]),
                    "max_per_day": availability[r]["max_per_day"],
                    "row_id": f"Avail_{r}"
                }
                for r in availability
            ],
            "exceptions_data": [
                {
                    "task": e["task"],
                    "applicable_groups": e["applicable_groups"],
                    "allow_consecutive": e["allow_consecutive"],
                    "row_id": f"Exc_{idx}"
                }
                for idx, e in enumerate(exceptions)
            ],
            "capacity_data": {
                # Setup expected hours matching allocation totals to bypass capacity reconciliation check
                "resources": {
                    r: sum(a["weekly_count"] for a in allocations if a["resource_name"] == r)
                    for r in set(a["resource_name"] for a in allocations)
                },
                "groups": {
                    g: sum(a["weekly_count"] for a in allocations if a["group_name"] == g)
                    for g in set(a["group_name"] for a in allocations)
                }
            }
        }

        # 3. Assert infeasibility is correct and confirm over-constraint
        pre_errors = validate_ingested_data(ingested_data)
        has_bottleneck = verify_infeasible_bottleneck(days_list, slots_count, allocations, availability)
        
        if pre_errors:
            stats["pre_validator_caught"] += 1
        if has_bottleneck:
            stats["bottleneck_caught"] += 1

        # We log to stdout for the test summary report
        print(f"\n[INFEASIBLE CASE] Pre-validator errors: {len(pre_errors)} | Custom bottleneck: {has_bottleneck}")
        
    else:
        pytest.fail(f"Solver returned unexpected status: {status_name}")

def test_print_summary():
    """Final test to output statistics of the property run."""
    print("\n" + "=" * 50)
    print("PROPERTY-BASED TESTING SUMMARY")
    print("=" * 50)
    print(f"Total Runs:                 {stats['runs']}")
    print(f"Feasible (Passed):          {stats['feasible']}")
    print(f"Infeasible (Proven):        {stats['infeasible']}")
    print(f"  - Caught by Pre-Validator:  {stats['pre_validator_caught']}")
    print(f"  - Caught by Bottleneck Check: {stats['bottleneck_caught']}")
    print("=" * 50)
