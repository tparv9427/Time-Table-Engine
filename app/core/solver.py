from typing import List, Dict, Any, Tuple, Optional
# pyrefly: ignore [missing-import]
import structlog
from ortools.sat.python import cp_model

logger = structlog.get_logger()

def solve_timetable(
    days_list: List[str],
    slots_count: int,
    allocations: List[Dict[str, Any]],
    availability: Dict[str, Dict[str, Any]],
    exceptions: List[Dict[str, Any]],
    class_teachers: Optional[Dict[str, str]] = None,
    time_limit_seconds: int = 30
) -> Tuple[str, List[Dict[str, Any]], int]:
    """
    Formulates and solves the weekly school timetable as a Constraint Satisfaction Problem.
    Returns:
      (status, schedule_records, objective_value)
      status is one of: "OPTIMAL", "FEASIBLE", "INFEASIBLE", "UNKNOWN"
    """
    model = cp_model.CpModel()
    
    num_days = len(days_list)
    num_slots = slots_count

    # Convert days to indexes (case-insensitive and trimmed)
    day_name_to_idx = {d.strip().lower(): i for i, d in enumerate(days_list)}

    # Normalise Availability mappings
    # If a resource isn't configured in availability, they default to full availability.
    resource_avail_map: Dict[str, Dict[str, Any]] = {}
    for res_name, avail in availability.items():
        allowed_days_idx = [day_name_to_idx[d.strip().lower()] for d in avail["allowed_days"] if d.strip().lower() in day_name_to_idx]
        # Convert 1-indexed slots to 0-indexed indexes, casting to int in case slots are represented as strings/floats
        allowed_slots_idx = [int(float(s)) - 1 for s in avail["allowed_slots"] if 0 <= int(float(s)) - 1 < num_slots]
        resource_avail_map[res_name.strip().lower()] = {
            "allowed_days": set(allowed_days_idx),
            "allowed_slots": set(allowed_slots_idx),
            "max_per_day": int(float(avail["max_per_day"]))
        }

    # Helper function to check if resource is available on a given day/slot
    def is_available(res: str, d: int, s: int) -> bool:
        res_key = res.strip().lower()
        if res_key not in resource_avail_map:
            return True # No constraints configured
        cfg = resource_avail_map[res_key]
        return d in cfg["allowed_days"] and s in cfg["allowed_slots"]

    # Gather resources, groups, tasks
    resources_set = set(a["resource_name"] for a in allocations)
    groups_set = set(a["group_name"] for a in allocations)
    tasks_set = set(a["task"] for a in allocations)

    # 1. Variables: x[res, task, group, day, slot]
    # To keep model size small, only create variables for active resource combinations
    # and only where the resource is available.
    x = {}
    
    # Track variables for resource/group/day/slot for constraints
    vars_by_resource: Dict[Tuple[str, int, int], List[Any]] = {}
    vars_by_group: Dict[Tuple[str, int, int], List[Any]] = {}
    vars_by_allocation: Dict[int, List[Any]] = {}

    for idx, alloc in enumerate(allocations):
        res = alloc["resource_name"]
        task = alloc["task"]
        grp = alloc["group_name"]

        vars_by_allocation[idx] = []

        for d in range(num_days):
            for s in range(num_slots):
                if not is_available(res, d, s):
                    continue

                # Create binary variable
                var_name = f"x_res_{res}_task_{task}_grp_{grp}_d_{d}_s_{s}"
                v = model.NewBoolVar(var_name)
                
                x[(res, task, grp, d, s)] = v
                
                vars_by_resource.setdefault((res, d, s), []).append(v)
                vars_by_group.setdefault((grp, d, s), []).append(v)
                vars_by_allocation[idx].append(v)

    # 2. Hard Constraints

    # (Constraint 1) Exact Count: Every allocation required count must be met exactly
    for idx, alloc in enumerate(allocations):
        req_count = alloc["weekly_count"]
        model.Add(sum(vars_by_allocation[idx]) == req_count)

    # (Constraint 2) No resource double-booked: At most 1 task/group per resource at (day, slot)
    for res in resources_set:
        for d in range(num_days):
            for s in range(num_slots):
                relevant_vars = vars_by_resource.get((res, d, s), [])
                if relevant_vars:
                    model.Add(sum(relevant_vars) <= 1)

    # (Constraint 3) No group double-booked: At most 1 task/resource per group at (day, slot)
    for grp in groups_set:
        for d in range(num_days):
            for s in range(num_slots):
                relevant_vars = vars_by_group.get((grp, d, s), [])
                if relevant_vars:
                    model.Add(sum(relevant_vars) <= 1)

    # (Constraint 4) Max per day for resources
    for res in resources_set:
        res_key = res.strip().lower()
        max_pd = resource_avail_map.get(res_key, {}).get("max_per_day", num_slots)
        for d in range(num_days):
            daily_vars = []
            for s in range(num_slots):
                daily_vars.extend(vars_by_resource.get((res, d, s), []))
            if daily_vars:
                model.Add(sum(daily_vars) <= max_pd)

    # (Constraint 5) No banned consecutive repetition within a group/day
    # Check if a consecutive task is explicitly permitted by exceptions.
    def is_consecutive_allowed(task_name: str, group_name: str) -> bool:
        for exc in exceptions:
            if exc["task"] == task_name:
                app_groups = [g.strip() for g in exc["applicable_groups"].split(",") if g.strip()]
                if exc["applicable_groups"] == "*" or group_name in app_groups:
                    return exc["allow_consecutive"]
        return False

    for grp in groups_set:
        for d in range(num_days):
            for s in range(num_slots - 1): # pairs: (s, s+1)
                for task in tasks_set:
                    if is_consecutive_allowed(task, grp):
                        continue
                    
                    # Sum of variables for this (task, group) at slot s across all resources
                    vars_slot_s = [
                        v for (r, t, g, day, slot), v in x.items()
                        if g == grp and t == task and day == d and slot == s
                    ]
                    # Sum of variables at slot s+1
                    vars_slot_sp1 = [
                        v for (r, t, g, day, slot), v in x.items()
                        if g == grp and t == task and day == d and slot == s + 1
                    ]
                    
                    if vars_slot_s and vars_slot_sp1:
                        model.Add(sum(vars_slot_s) + sum(vars_slot_sp1) <= 1)

    # 3. Soft Constraints (Objective: balance daily load per resource)
    objective_terms = []
    for res in resources_set:
        # Calculate total weekly assignments requested for this resource
        total_slots_needed = sum(a["weekly_count"] for a in allocations if a["resource_name"] == res)
        if total_slots_needed <= 0:
            continue
            
        avail = resource_avail_map.get(res, {
            "allowed_days": set(range(num_days)),
            "allowed_slots": set(range(num_slots)),
            "max_per_day": num_slots
        })
        active_days = len(avail["allowed_days"])
        if active_days == 0:
            continue

        target = total_slots_needed / active_days
        
        for d in avail["allowed_days"]:
            daily_vars = []
            for s in range(num_slots):
                daily_vars.extend(vars_by_resource.get((res, d, s), []))
                
            if not daily_vars:
                continue

            day_count = model.NewIntVar(0, num_slots, f"cnt_{res}_{d}")
            model.Add(day_count == sum(daily_vars))
            
            dev = model.NewIntVar(0, num_slots, f"dev_{res}_{d}")
            # Create a helper diff variable to handle absolute equality correctly on all OR-Tools versions
            diff = model.NewIntVar(-num_slots, num_slots, f"diff_{res}_{d}")
            model.Add(diff == day_count - round(target))
            model.AddAbsEquality(dev, diff)
            objective_terms.append(dev)

    # Class teacher first slot soft constraint:
    # If teacher T is the class teacher of group G, we reward having T assigned to G at slot=0 (first slot) for each day.
    if class_teachers:
        for res, grp in class_teachers.items():
            if not grp:
                continue
            for d in range(num_days):
                # find all decision variables for this resource, group, day, slot=0 (using case-insensitive comparison)
                first_slot_vars = [
                    v for (r, t, g, day, slot), v in x.items()
                    if r.strip().lower() == res.strip().lower() and g.strip().lower() == grp.strip().lower() and day == d and slot == 0
                ]
                if first_slot_vars:
                    # we want to maximize sum(first_slot_vars), which is <= 1.
                    # so we minimize (1 - sum(first_slot_vars))
                    not_first_slot = model.NewBoolVar(f"not_first_slot_{res}_{d}")
                    model.Add(not_first_slot + sum(first_slot_vars) == 1)
                    objective_terms.append(10 * not_first_slot)

    if objective_terms:
        model.Minimize(sum(objective_terms))

    # 4. Solves
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit_seconds
    solver.parameters.num_search_workers = 4 # Enable multithreaded solving

    logger.info("solver_started", variables=len(x), time_limit=time_limit_seconds)
    status = solver.Solve(model)
    logger.info("solver_finished", status=solver.StatusName(status))

    # Parse status
    status_name = solver.StatusName(status) # OPTIMAL, FEASIBLE, INFEASIBLE, UNKNOWN
    
    schedule_records = []
    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        # Reconstruct schedule from decisions
        for (res, task, grp, d, s), var in x.items():
            if solver.Value(var) == 1:
                schedule_records.append({
                    "day": d,
                    "slot": s,
                    "resource_name": res,
                    "group_name": grp,
                    "task": task
                })

    return status_name, schedule_records, int(solver.ObjectiveValue()) if status in (cp_model.OPTIMAL, cp_model.FEASIBLE) else 0
