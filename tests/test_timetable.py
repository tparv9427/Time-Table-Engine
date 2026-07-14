import pytest
from app.core.pre_validator import validate_ingested_data
from app.core.post_validator import validate_schedule_output
from app.core.solver import solve_timetable

def test_pre_validator_consistent():
    # Valid input data mock
    valid_data = {
        "config": {
            "Day Names": "Mon,Tue",
            "Slots Per Day": "4"
        },
        "resources_data": [
            {"resource_name": "Teacher A", "task": "Math", "home_group": None, "group_name": "Class X", "weekly_count": 2, "row_id": "Row 2"}
        ],
        "availability_data": [
            {"resource_name": "Teacher A", "allowed_days": "Mon,Tue", "allowed_slots": "1,2,3,4", "max_per_day": 2, "row_id": "Row 2"}
        ],
        "exceptions_data": [],
        "capacity_data": {
            "resources": {"Teacher A": 2},
            "groups": {"Class X": 2}
        }
    }
    
    errors = validate_ingested_data(valid_data)
    assert len(errors) == 0, f"Expected 0 validation errors, got: {errors}"

def test_pre_validator_impossibility():
    # Invalid: Teacher A demands 10 slots but only has 2 days * 4 slots = 8 slots capacity
    invalid_data = {
        "config": {
            "Day Names": "Mon,Tue",
            "Slots Per Day": "4"
        },
        "resources_data": [
            {"resource_name": "Teacher A", "task": "Math", "home_group": None, "group_name": "Class X", "weekly_count": 10, "row_id": "Row 2"}
        ],
        "availability_data": [
            {"resource_name": "Teacher A", "allowed_days": "Mon,Tue", "allowed_slots": "1,2,3,4", "max_per_day": 4, "row_id": "Row 2"}
        ],
        "exceptions_data": [],
        "capacity_data": {
            "resources": {"Teacher A": 10},
            "groups": {"Class X": 10}
        }
    }
    
    errors = validate_ingested_data(invalid_data)
    assert any("exceeds total available slots" in err for err in errors), "Expected math impossibility warning"

def test_post_validator_violations():
    # Create a broken schedule (Teacher A double booked on Mon, Slot 1)
    days_list = ["Mon", "Tue"]
    slots_count = 4
    allocations = [
        {"resource_name": "Teacher A", "group_name": "Class X", "task": "Math", "weekly_count": 2},
        {"resource_name": "Teacher A", "group_name": "Class Y", "task": "English", "weekly_count": 1}
    ]
    availability = {
        "Teacher A": {"allowed_days": ["Mon", "Tue"], "allowed_slots": [1, 2, 3, 4], "max_per_day": 4}
    }
    exceptions = []
    
    # Broken schedule: Teacher A at same slot in Class X and Class Y
    broken_schedule = [
        {"day": 0, "slot": 0, "resource_name": "Teacher A", "group_name": "Class X", "task": "Math"},
        {"day": 0, "slot": 0, "resource_name": "Teacher A", "group_name": "Class Y", "task": "English"}
    ]
    
    violations = validate_schedule_output(days_list, slots_count, allocations, availability, exceptions, broken_schedule)
    assert any("double booking" in v.lower() for v in violations), "Expected double booking violation to be caught"

def test_solver_optimal_run():
    # Simple valid CSP
    days_list = ["Mon", "Tue"]
    slots_count = 2
    allocations = [
        {"resource_name": "Teacher A", "group_name": "Class X", "task": "Math", "weekly_count": 2},
        {"resource_name": "Teacher B", "group_name": "Class Y", "task": "Science", "weekly_count": 2}
    ]
    availability = {}
    exceptions = []
    
    status, schedule, obj = solve_timetable(days_list, slots_count, allocations, availability, exceptions)
    assert status in ["OPTIMAL", "FEASIBLE"]
    assert len(schedule) == 4
