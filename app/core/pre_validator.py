from typing import List, Dict, Any, Set
import structlog
from app.services.ingestion import clean_str, clean_int

logger = structlog.get_logger()

def validate_ingested_data(data: Dict[str, Any]) -> List[str]:
    """
    Validates the parsed Excel template data.
    Returns a list of detailed, actionable validation error messages.
    An empty list indicates the data is completely consistent and mathematically plausible.
    """
    errors = []
    
    config = data.get("config", {})
    resources_data = data.get("resources_data", [])
    availability_data = data.get("availability_data", [])
    exceptions_data = data.get("exceptions_data", [])
    capacity_data = data.get("capacity_data", {})

    # 1. Parse & validate basic config settings
    day_names_raw = config.get("Day Names", "")
    days_list = [d.strip() for d in day_names_raw.split(",") if d.strip()]
    if not days_list:
        errors.append("Organization Config: 'Day Names' must contain at least one valid day (e.g. Mon,Tue,Wed)")
        return errors # Stop early since config is critical
        
    slots_per_day = clean_int(config.get("Slots Per Day"))
    if slots_per_day <= 0:
        errors.append(f"Organization Config: 'Slots Per Day' must be a positive integer (found: {config.get('Slots Per Day')})")
        return errors

    # Gather resources, groups, tasks defined in resources data
    defined_resources: Set[str] = set()
    defined_groups: Set[str] = set()
    resource_weekly_sums: Dict[str, int] = {}
    group_weekly_sums: Dict[str, int] = {}

    # 2. Validate Resources Data
    for row in resources_data:
        row_id = row["row_id"]
        res_name = row["resource_name"]
        task = row["task"]
        group_name = row["group_name"]
        weekly_count = row["weekly_count"]

        if not res_name:
            errors.append(f"{row_id}: Missing 'Resource Name'")
        if not task:
            errors.append(f"{row_id}: Missing 'Task/Subject'")
        if not group_name:
            errors.append(f"{row_id}: Missing 'Group'")
            
        # Count check
        try:
            val = float(str(weekly_count).strip())
            if not val.is_integer() or val <= 0:
                errors.append(f"{row_id}: 'Weekly Count' must be a positive integer (found: {weekly_count})")
                count_int = 0
            else:
                count_int = int(val)
        except Exception:
            errors.append(f"{row_id}: 'Weekly Count' must be a positive integer (found: {weekly_count})")
            count_int = 0

        if res_name:
            defined_resources.add(res_name)
            resource_weekly_sums[res_name] = resource_weekly_sums.get(res_name, 0) + count_int
        if group_name:
            defined_groups.add(group_name)
            group_weekly_sums[group_name] = group_weekly_sums.get(group_name, 0) + count_int

    # 3. Validate Availability and track constraints
    availability_records: Dict[str, Dict[str, Any]] = {}
    availability_seen: Set[str] = set()

    for row in availability_data:
        row_id = row["row_id"]
        res_name = row["resource_name"]
        raw_days = row["allowed_days"]
        raw_slots = row["allowed_slots"]
        max_per_day_val = row["max_per_day"]

        if not res_name:
            errors.append(f"{row_id}: Missing 'Resource Name'")
            continue

        if res_name not in defined_resources:
            errors.append(f"{row_id}: Resource '{res_name}' is not defined in the 'Resources Data' sheet")

        if res_name in availability_seen:
            errors.append(f"{row_id}: Duplicate availability entry for resource '{res_name}'")
        availability_seen.add(res_name)

        # Parse days
        row_days = []
        if raw_days:
            row_days = [d.strip() for d in str(raw_days).split(",") if d.strip()]
            # Ensure they are subset of days_list
            for d in row_days:
                if d not in days_list:
                    errors.append(f"{row_id}: Allowed day '{d}' is not in configured 'Day Names' ({day_names_raw})")
        else:
            row_days = days_list.copy() # Default to all days

        # Parse slots
        row_slots = []
        if raw_slots:
            try:
                row_slots = [int(float(s.strip())) for s in str(raw_slots).split(",") if s.strip()]
                # Validate range
                for s in row_slots:
                    if s < 1 or s > slots_per_day:
                        errors.append(f"{row_id}: Allowed slot '{s}' is out of configured range 1-{slots_per_day}")
            except ValueError:
                errors.append(f"{row_id}: 'Allowed Slots' must be a comma-separated list of integers (found: {raw_slots})")
        else:
            row_slots = list(range(1, slots_per_day + 1)) # Default to all slots

        # Max per day
        max_per_day = slots_per_day
        if max_per_day_val is not None:
            max_per_day = clean_int(max_per_day_val)
            if max_per_day <= 0 or max_per_day > slots_per_day:
                errors.append(f"{row_id}: 'Max Per Day' must be between 1 and {slots_per_day} (found: {max_per_day_val})")

        availability_records[res_name] = {
            "allowed_days": row_days,
            "allowed_slots": row_slots,
            "max_per_day": max_per_day
        }

    # 4. Mathematical feasibility checks per resource
    for res_name in defined_resources:
        weekly_demand = resource_weekly_sums.get(res_name, 0)
        
        # Get availability (or default to all days/slots if none specified)
        avail = availability_records.get(res_name, {
            "allowed_days": days_list,
            "allowed_slots": list(range(1, slots_per_day + 1)),
            "max_per_day": slots_per_day
        })

        allowed_days_count = len(avail["allowed_days"])
        allowed_slots_count = len(avail["allowed_slots"])
        max_per_day = avail["max_per_day"]

        # If resource has nonzero demand but empty availability sets
        if weekly_demand > 0:
            if allowed_days_count == 0:
                errors.append(f"Resource '{res_name}': Requires {weekly_demand} slots but has 0 allowed days.")
            if allowed_slots_count == 0:
                errors.append(f"Resource '{res_name}': Requires {weekly_demand} slots but has 0 allowed slots.")

        # Crucial CSP bounding check:
        # 1. Total weekly assignments cannot exceed the physical number of open availability slots
        total_open_slots = allowed_days_count * allowed_slots_count
        if weekly_demand > total_open_slots:
            errors.append(f"Resource '{res_name}': Math Impossibility. Weekly demand ({weekly_demand}) exceeds total available slots ({total_open_slots} = {allowed_days_count} days * {allowed_slots_count} slots)")
            
        # 2. Total weekly assignments cannot exceed max_per_day * allowed_days
        max_possible_weekly = max_per_day * allowed_days_count
        if weekly_demand > max_possible_weekly:
            errors.append(f"Resource '{res_name}': Math Impossibility. Weekly demand ({weekly_demand}) exceeds maximum allowed weekly capacity ({max_possible_weekly} = {max_per_day} max/day * {allowed_days_count} days)")

    # 5. Group capacity boundary check (cannot exceed slots_per_day * total_days)
    max_group_capacity = slots_per_day * len(days_list)
    for group_name, demand in group_weekly_sums.items():
        if demand > max_group_capacity:
            errors.append(f"Group '{group_name}': Weekly demand ({demand}) exceeds the total available slots in a week ({max_group_capacity} = {slots_per_day} slots/day * {len(days_list)} days)")

    # 6. Validate Exceptions
    for row in exceptions_data:
        row_id = row["row_id"]
        task = row["task"]
        app_groups = row["applicable_groups"]

        if not task:
            errors.append(f"{row_id}: Missing 'Task/Subject'")
            
        if app_groups and app_groups != "*":
            groups_list = [g.strip() for g in app_groups.split(",") if g.strip()]
            for g in groups_list:
                if g not in defined_groups:
                    errors.append(f"{row_id}: Exception references group '{g}' which is not in 'Resources Data'")

    # 7. Validate Capacity Reconciliation
    cap_res = capacity_data.get("resources", {})
    cap_groups = capacity_data.get("groups", {})

    for name, expected_hours in cap_res.items():
        actual = resource_weekly_sums.get(name, 0)
        expected = clean_int(expected_hours)
        if actual != expected:
            errors.append(f"Capacity Reconciliation: Resource '{name}' expected weekly hours ({expected}) does not reconcile with total in Resources Data ({actual})")

    for name, expected_hours in cap_groups.items():
        actual = group_weekly_sums.get(name, 0)
        expected = clean_int(expected_hours)
        if actual != expected:
            errors.append(f"Capacity Reconciliation: Group '{name}' expected weekly hours ({expected}) does not reconcile with total in Resources Data ({actual})")

    return errors
