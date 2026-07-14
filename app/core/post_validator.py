from typing import List, Dict, Any

def validate_schedule_output(
    days_list: List[str],
    slots_count: int,
    allocations: List[Dict[str, Any]],
    availability: Dict[str, Dict[str, Any]],
    exceptions: List[Dict[str, Any]],
    schedule: List[Dict[str, Any]]
) -> List[str]:
    """
    Independent post-solve validator checking every hard constraint directly from output.
    Returns a list of violation description strings. Empty list = fully valid.
    """
    violations = []
    
    num_days = len(days_list)
    num_slots = slots_count
    day_name_to_idx = {d: i for i, d in enumerate(days_list)}

    # Track actual counts for allocations
    # Key: (resource_name, task, group_name)
    actual_allocation_counts = {}
    for item in schedule:
        key = (item["resource_name"], item["task"], item["group_name"])
        actual_allocation_counts[key] = actual_allocation_counts.get(key, 0) + 1

    # Rule 1: Every allocation's actual count matches required count exactly
    for alloc in allocations:
        res = alloc["resource_name"]
        task = alloc["task"]
        grp = alloc["group_name"]
        req = alloc["weekly_count"]
        
        actual = actual_allocation_counts.get((res, task, grp), 0)
        if actual != req:
            violations.append(
                f"Allocation mismatch: {res} -> {task} for {grp} requires {req} weekly slots, but actually got {actual} assignments."
            )

    # Track slot occupancy for resources and groups
    # Key: (resource_name, day, slot) -> [item]
    resource_occupancy = {}
    # Key: (group_name, day, slot) -> [item]
    group_occupancy = {}
    # Key: (resource, day) -> count
    resource_daily_counts = {}

    for item in schedule:
        res = item["resource_name"]
        grp = item["group_name"]
        d = item["day"]
        s = item["slot"]
        task = item["task"]

        # Track resource occupancy
        res_key = (res, d, s)
        resource_occupancy.setdefault(res_key, []).append(item)

        # Track group occupancy
        grp_key = (grp, d, s)
        group_occupancy.setdefault(grp_key, []).append(item)

        # Track daily counts
        day_key = (res, d)
        resource_daily_counts[day_key] = resource_daily_counts.get(day_key, 0) + 1

    # Rule 2: No resource appears twice in the same (day, slot)
    for (res, d, s), assignments in resource_occupancy.items():
        if len(assignments) > 1:
            violations.append(
                f"Resource double booking: Teacher '{res}' is scheduled to multiple classes in Day {d}, Slot {s + 1}."
            )

    # Rule 3: No group appears twice in the same (day, slot)
    for (grp, d, s), assignments in group_occupancy.items():
        if len(assignments) > 1:
            violations.append(
                f"Group double booking: Class '{grp}' has multiple lessons scheduled in Day {d}, Slot {s + 1}."
            )

    # Rule 4: Every assignment falls within its resource's allowed days/slots
    for item in schedule:
        res = item["resource_name"]
        d = item["day"]
        s = item["slot"]

        if res in availability:
            avail = availability[res]
            # Convert allowed day names to indexes for checking
            allowed_days_idx = {day_name_to_idx[day] for day in avail["allowed_days"] if day in day_name_to_idx}
            # Convert 1-indexed slots to 0-indexed for checking
            allowed_slots_idx = {slot - 1 for slot in avail["allowed_slots"]}

            if d not in allowed_days_idx:
                violations.append(
                    f"Availability violation: '{res}' assigned on Day {d} which is not in their allowed days."
                )
            if s not in allowed_slots_idx:
                violations.append(
                    f"Availability violation: '{res}' assigned to Slot {s + 1} which is not in their allowed slots."
                )

    # Rule 5: No resource's daily count exceeds its max-per-day cap
    for (res, d), count in resource_daily_counts.items():
        if res in availability:
            cap = availability[res]["max_per_day"]
            if count > cap:
                violations.append(
                    f"Daily capacity exceeded: '{res}' has {count} classes assigned on Day {d}, exceeding their daily limit of {cap}."
                )

    # Rule 6: No banned consecutive same-task repetition within any group/day
    def is_consecutive_allowed(task_name: str, group_name: str) -> bool:
        for exc in exceptions:
            if exc["task"] == task_name:
                app_groups = [g.strip() for g in exc["applicable_groups"].split(",") if g.strip()]
                if exc["applicable_groups"] == "*" or group_name in app_groups:
                    return exc["allow_consecutive"]
        return False

    # Check for consecutive tasks in group timetables
    # We reconstruct the group's timetable per day
    for grp in set(item["group_name"] for item in schedule):
        for d in range(num_days):
            # Get tasks scheduled for this group on this day, ordered by slot
            day_schedule = [None] * num_slots
            for item in schedule:
                if item["group_name"] == grp and item["day"] == d:
                    day_schedule[item["slot"]] = item["task"]
            
            # Check adjacent slots
            for s in range(num_slots - 1):
                t1 = day_schedule[s]
                t2 = day_schedule[s+1]
                if t1 is not None and t1 == t2:
                    if not is_consecutive_allowed(t1, grp):
                        violations.append(
                            f"Consecutive violation: Class '{grp}' has consecutive lessons of '{t1}' in Day {d}, Slots {s+1} & {s+2}."
                        )

    return violations
