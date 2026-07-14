from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, WebSocket, WebSocketDisconnect, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from uuid import UUID
from typing import List, Dict, Any
import json
import structlog
from datetime import datetime

from app.core.database import get_db
from app.models.models import (
    Organization, Resource, Group, Allocation, Availability, TaskException, Schedule, ScheduleGenerationLog
)
from app.models.schemas import OrganizationCreate, OrganizationOut, ScheduleEdit, ScheduleOut, ScheduleGenerationLogOut, WizardSetupInput
from app.services.ingestion import parse_user_excel, IngestionError
from app.core.pre_validator import validate_ingested_data
from app.core.solver import solve_timetable
from app.core.post_validator import validate_schedule_output

logger = structlog.get_logger()
router = APIRouter()

# Active WebSocket connections by organization_id
class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, List[WebSocket]] = {}

    async def connect(self, org_id: str, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.setdefault(org_id, []).append(websocket)
        logger.info("websocket_connected", org_id=org_id)

    def disconnect(self, org_id: str, websocket: WebSocket):
        if org_id in self.active_connections:
            self.active_connections[org_id].remove(websocket)
            logger.info("websocket_disconnected", org_id=org_id)

    async def broadcast(self, org_id: str, message: dict):
        if org_id in self.active_connections:
            for connection in self.active_connections[org_id]:
                try:
                    await connection.send_json(message)
                except Exception as e:
                    logger.error("websocket_send_failed", error=str(e))

manager = ConnectionManager()

# In-memory storage for last uploaded template status (simplifies design spec GET /template/status)
# format: { org_id_str: {"status": "VALID"|"INVALID"|"NONE", "errors": [...] } }
template_statuses: Dict[str, Dict[str, Any]] = {}

@router.post("/organizations", response_model=OrganizationOut)
async def create_organization(payload: OrganizationCreate, db: AsyncSession = Depends(get_db)):
    org = Organization(name=payload.name, subdomain=payload.subdomain, config={})
    db.add(org)
    await db.commit()
    await db.refresh(org)
    return org

@router.post("/organizations/setup-wizard")
async def setup_wizard(payload: WizardSetupInput, db: AsyncSession = Depends(get_db)):
    import re
    import random
    
    # 1. Create Organization with derived subdomain
    subdomain = re.sub(r'[^a-zA-Z0-9-]', '', payload.org_name.lower().replace(" ", "-"))
    existing = await db.execute(select(Organization).where(Organization.subdomain == subdomain))
    if existing.scalar_one_or_none():
        subdomain += f"-{random.randint(100, 999)}"

    org = Organization(
        name=payload.org_name,
        subdomain=subdomain,
        config={
            "day_names": payload.working_days,
            "slots_per_day": payload.slots_count
        }
    )
    db.add(org)
    await db.flush() # populate org.id

    # 2. Insert Classes (Groups)
    group_map = {}
    for grp_name in payload.classes:
        grp = Group(organization_id=org.id, name=grp_name)
        db.add(grp)
        group_map[grp_name] = grp

    # 3. Insert Teachers (Resources)
    resource_map = {}
    class_teachers = {}
    for teacher in payload.teachers:
        name = teacher.name
        if name not in resource_map:
            home_group = teacher.is_class_teacher_of if teacher.is_class_teacher_of in group_map else None
            res = Resource(organization_id=org.id, name=name, home_group=home_group)
            db.add(res)
            resource_map[name] = res
            
            if home_group:
                class_teachers[name] = home_group

    await db.flush()

    # 4. Insert Allocations & Availability
    for teacher in payload.teachers:
        res_obj = resource_map[teacher.name]
        grp_obj = group_map[teacher.target_class]
        
        alloc = Allocation(
            organization_id=org.id,
            resource_id=res_obj.id,
            group_id=grp_obj.id,
            task=teacher.subject,
            weekly_count=teacher.weekly_count
        )
        db.add(alloc)

        allowed_days = teacher.allowed_days if teacher.allowed_days else payload.working_days
        allowed_slots = teacher.allowed_slots if teacher.allowed_slots else list(range(1, payload.slots_count + 1))
        max_per_day = teacher.max_per_day if teacher.max_per_day is not None else payload.slots_count

        avail = Availability(
            organization_id=org.id,
            resource_id=res_obj.id,
            allowed_days=allowed_days,
            allowed_slots=allowed_slots,
            max_per_day=max_per_day
        )
        db.add(avail)

    await db.flush()

    # 5. Build raw arrays for Solver
    allocations_arr = []
    for teacher in payload.teachers:
        allocations_arr.append({
            "resource_name": teacher.name,
            "group_name": teacher.target_class,
            "task": teacher.subject,
            "weekly_count": teacher.weekly_count
        })

    availability_arr = {}
    for teacher in payload.teachers:
        allowed_days = teacher.allowed_days if teacher.allowed_days else payload.working_days
        allowed_slots = teacher.allowed_slots if teacher.allowed_slots else list(range(1, payload.slots_count + 1))
        max_per_day = teacher.max_per_day if teacher.max_per_day is not None else payload.slots_count
        availability_arr[teacher.name] = {
            "allowed_days": allowed_days,
            "allowed_slots": allowed_slots,
            "max_per_day": max_per_day
        }

    exceptions_arr = [] # none by default in wizard

    # 6. Run solver with class teachers soft constraint
    solver_status, raw_schedule, obj_val = solve_timetable(
        days_list=payload.working_days,
        slots_count=payload.slots_count,
        allocations=allocations_arr,
        availability=availability_arr,
        exceptions=exceptions_arr,
        class_teachers=class_teachers
    )

    violations = []
    if solver_status in ["OPTIMAL", "FEASIBLE"]:
        violations = validate_schedule_output(
            days_list=payload.working_days,
            slots_count=payload.slots_count,
            allocations=allocations_arr,
            availability=availability_arr,
            exceptions=exceptions_arr,
            schedule=raw_schedule
        )

    log_status = "SUCCESS" if (solver_status in ["OPTIMAL", "FEASIBLE"] and not violations) else "FAILED"
    gen_log = ScheduleGenerationLog(
        organization_id=org.id,
        status=log_status,
        violations=violations if violations else None,
        solved_at=datetime.utcnow(),
        solver_status=solver_status
    )
    db.add(gen_log)

    if log_status == "FAILED":
        await db.rollback()
        error_msg = f"Timetable generation failed. Solver verdict: {solver_status}."
        if violations:
            error_msg += f" Violations: {'; '.join(violations)}"
        raise HTTPException(status_code=400, detail={"message": error_msg, "violations": violations})

    # Save schedule
    for item in raw_schedule:
        sch = Schedule(
            organization_id=org.id,
            day=item["day"],
            slot=item["slot"],
            resource_id=resource_map[item["resource_name"]].id,
            group_id=group_map[item["group_name"]].id,
            task=item["task"]
        )
        db.add(sch)

    await db.commit()
    
    return {
        "status": "success",
        "organization_id": str(org.id),
        "organization_name": org.name,
        "subdomain": org.subdomain
    }

@router.post("/organizations/upload-roster")
async def upload_roster(
    org_name: str = Form(...),
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db)
):
    import re
    import random
    
    # 1. Create Organization with derived subdomain
    subdomain = re.sub(r'[^a-zA-Z0-9-]', '', org_name.lower().replace(" ", "-"))
    existing = await db.execute(select(Organization).where(Organization.subdomain == subdomain))
    if existing.scalar_one_or_none():
        subdomain += f"-{random.randint(100, 999)}"

    contents = await file.read()
    
    # 2. Ingest excel data
    try:
        data = parse_user_excel(contents)
    except IngestionError as e:
        raise HTTPException(status_code=422, detail={"errors": e.errors})
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed parsing file structure: {str(e)}")

    # 3. Pre-solve validate
    pre_errors = validate_ingested_data(data)
    if pre_errors:
        raise HTTPException(status_code=422, detail={"errors": pre_errors})

    org = Organization(
        name=org_name,
        subdomain=subdomain,
        config={
            "day_names": [d.strip() for d in data["config"]["Day Names"].split(",") if d.strip()],
            "slots_per_day": data["config"]["Slots Per Day"]
        }
    )
    db.add(org)
    await db.flush()

    try:
        # Insert resources, groups, allocations, availability
        resource_map = {}
        group_map = {}
        class_teachers = {}

        # Resources & Groups
        for row in data["resources_data"]:
            r_name = row["resource_name"]
            g_name = row["group_name"]
            home_group = row["home_group"]

            if r_name not in resource_map:
                res = Resource(organization_id=org.id, name=r_name, home_group=home_group)
                db.add(res)
                resource_map[r_name] = res
                
                if home_group:
                    class_teachers[r_name] = home_group

            if g_name not in group_map:
                grp = Group(organization_id=org.id, name=g_name)
                db.add(grp)
                group_map[g_name] = grp

        await db.flush()

        # Allocations
        for row in data["resources_data"]:
            alloc = Allocation(
                organization_id=org.id,
                resource_id=resource_map[row["resource_name"]].id,
                group_id=group_map[row["group_name"]].id,
                task=row["task"],
                weekly_count=row["weekly_count"]
            )
            db.add(alloc)

        # Availability
        for row in data["availability_data"]:
            avail = Availability(
                organization_id=org.id,
                resource_id=resource_map[row["resource_name"]].id,
                allowed_days=[d.strip() for d in str(row["allowed_days"]).split(",") if d.strip()],
                allowed_slots=[int(float(s.strip())) for s in str(row["allowed_slots"]).split(",") if s.strip()],
                max_per_day=row["max_per_day"]
            )
            db.add(avail)

        await db.flush()

        # 4. Trigger solver!
        days_list = org.config["day_names"]
        slots_count = org.config["slots_per_day"]

        allocations_arr = [{
            "resource_name": r["resource_name"],
            "group_name": r["group_name"],
            "task": r["task"],
            "weekly_count": r["weekly_count"]
        } for r in data["resources_data"]]

        availability_arr = {}
        for r in data["availability_data"]:
            availability_arr[r["resource_name"]] = {
                "allowed_days": [d.strip() for d in str(r["allowed_days"]).split(",") if d.strip()],
                "allowed_slots": [int(float(s.strip())) for s in str(r["allowed_slots"]).split(",") if s.strip()],
                "max_per_day": r["max_per_day"]
            }

        exceptions_arr = []

        solver_status, raw_schedule, obj_val = solve_timetable(
            days_list=days_list,
            slots_count=slots_count,
            allocations=allocations_arr,
            availability=availability_arr,
            exceptions=exceptions_arr,
            class_teachers=class_teachers
        )

        violations = []
        if solver_status in ["OPTIMAL", "FEASIBLE"]:
            violations = validate_schedule_output(
                days_list=days_list,
                slots_count=slots_count,
                allocations=allocations_arr,
                availability=availability_arr,
                exceptions=exceptions_arr,
                schedule=raw_schedule
            )

        log_status = "SUCCESS" if (solver_status in ["OPTIMAL", "FEASIBLE"] and not violations) else "FAILED"
        gen_log = ScheduleGenerationLog(
            organization_id=org.id,
            status=log_status,
            violations=violations if violations else None,
            solved_at=datetime.utcnow(),
            solver_status=solver_status
        )
        db.add(gen_log)

        if log_status == "FAILED":
            await db.rollback()
            error_msg = f"Roster constraint solving failed. Verdict: {solver_status}."
            if violations:
                error_msg += f" Violations: {'; '.join(violations)}"
            raise HTTPException(status_code=400, detail={"message": error_msg, "violations": violations})

        # Save schedule
        for item in raw_schedule:
            sch = Schedule(
                organization_id=org.id,
                day=item["day"],
                slot=item["slot"],
                resource_id=resource_map[item["resource_name"]].id,
                group_id=group_map[item["group_name"]].id,
                task=item["task"]
            )
            db.add(sch)

        await db.commit()
        return {
            "status": "success",
            "organization_id": str(org.id),
            "organization_name": org.name,
            "subdomain": org.subdomain
        }

    except Exception as e:
        await db.rollback()
        raise e

@router.get("/organizations", response_model=List[OrganizationOut])
async def list_organizations(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Organization))
    return list(result.scalars().all())

@router.post("/organizations/{org_id}/template")
async def upload_template(org_id: UUID, file: UploadFile = File(...), db: AsyncSession = Depends(get_db)):
    # 1. Fetch organization
    org = await db.get(Organization, org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    contents = await file.read()
    
    # 2. Parse workbook
    try:
        data = parse_template(contents)
    except IngestionError as e:
        template_statuses[str(org_id)] = {"status": "INVALID", "errors": e.errors}
        raise HTTPException(status_code=422, detail={"errors": e.errors})
    except Exception as e:
        template_statuses[str(org_id)] = {"status": "INVALID", "errors": [f"Failure parsing file: {str(e)}"]}
        raise HTTPException(status_code=400, detail=f"Failed to process template: {str(e)}")

    # 3. Pre-solve validate
    pre_errors = validate_ingested_data(data)
    if pre_errors:
        template_statuses[str(org_id)] = {"status": "INVALID", "errors": pre_errors}
        return {"status": "validation_failed", "errors": pre_errors}

    # 4. Save parsed elements
    try:
        # Save config directly on organization
        org.config = {
            "day_names": [d.strip() for d in data["config"]["Day Names"].split(",") if d.strip()],
            "slots_per_day": int(float(data["config"]["Slots Per Day"]))
        }
        db.add(org)

        # Clear existing models for this organization
        await db.execute(delete(Resource).where(Resource.organization_id == org_id))
        await db.execute(delete(Group).where(Group.organization_id == org_id))
        await db.execute(delete(Allocation).where(Allocation.organization_id == org_id))
        await db.execute(delete(Availability).where(Availability.organization_id == org_id))
        await db.execute(delete(TaskException).where(TaskException.organization_id == org_id))
        await db.execute(delete(Schedule).where(Schedule.organization_id == org_id))

        # Insert new items
        resource_map = {}
        group_map = {}

        # Resources Data
        for row in data["resources_data"]:
            name = row["resource_name"]
            if name not in resource_map:
                res = Resource(organization_id=org_id, name=name, home_group=row["home_group"])
                db.add(res)
                resource_map[name] = res

        # Groups Data
        for row in data["resources_data"]:
            grp_name = row["group_name"]
            if grp_name not in group_map:
                grp = Group(organization_id=org_id, name=grp_name)
                db.add(grp)
                group_map[grp_name] = grp

        # Flush to get IDs
        await db.flush()

        # Allocations
        for row in data["resources_data"]:
            alloc = Allocation(
                organization_id=org_id,
                resource_id=resource_map[row["resource_name"]].id,
                group_id=group_map[row["group_name"]].id,
                task=row["task"],
                weekly_count=int(float(str(row["weekly_count"])))
            )
            db.add(alloc)

        # Availability
        for row in data["availability_data"]:
            avail = Availability(
                organization_id=org_id,
                resource_id=resource_map[row["resource_name"]].id,
                allowed_days=[d.strip() for d in str(row["allowed_days"]).split(",") if d.strip()],
                allowed_slots=[int(float(s.strip())) for s in str(row["allowed_slots"]).split(",") if s.strip()],
                max_per_day=int(float(str(row["max_per_day"])))
            )
            db.add(avail)

        # Task Exceptions
        for row in data["exceptions_data"]:
            exc = TaskException(
                organization_id=org_id,
                task=row["task"],
                applicable_groups=[g.strip() for g in str(row["applicable_groups"]).split(",") if g.strip()],
                allow_consecutive=row["allow_consecutive"]
            )
            db.add(exc)

        await db.commit()
        template_statuses[str(org_id)] = {"status": "VALID", "errors": []}
        return {"status": "success", "message": "Template uploaded and verified successfully."}

    except Exception as e:
        await db.rollback()
        logger.error("template_save_failed", error=str(e))
        template_statuses[str(org_id)] = {"status": "INVALID", "errors": [f"Database saving failed: {str(e)}"]}
        raise HTTPException(status_code=500, detail=f"Database update failed: {str(e)}")

@router.get("/organizations/{org_id}/template/status")
async def get_template_status(org_id: UUID):
    status_info = template_statuses.get(str(org_id), {"status": "NONE", "errors": []})
    return status_info

@router.post("/organizations/{org_id}/schedule/generate")
async def generate_schedule(org_id: UUID, db: AsyncSession = Depends(get_db)):
    org = await db.get(Organization, org_id)
    if not org or not org.config:
        raise HTTPException(status_code=404, detail="Organization or configuration not found. Upload template first.")

    days_list = org.config.get("day_names", [])
    slots_count = org.config.get("slots_per_day", 0)

    # 1. Fetch solver parameters from DB
    # Fetch Allocations
    alloc_res = await db.execute(
        select(Allocation, Resource.name, Group.name).join(Resource).join(Group).where(Allocation.organization_id == org_id)
    )
    allocations = []
    alloc_obj_map = {}
    for alloc, r_name, g_name in alloc_res.all():
        allocations.append({
            "resource_name": r_name,
            "group_name": g_name,
            "task": alloc.task,
            "weekly_count": alloc.weekly_count
        })
        alloc_obj_map[(r_name, alloc.task, g_name)] = alloc

    # Fetch Availability
    avail_res = await db.execute(
        select(Availability, Resource.name).join(Resource).where(Availability.organization_id == org_id)
    )
    availability = {}
    for avail, r_name in avail_res.all():
        availability[r_name] = {
            "allowed_days": avail.allowed_days,
            "allowed_slots": avail.allowed_slots,
            "max_per_day": avail.max_per_day
        }

    # Fetch Exceptions
    exc_res = await db.execute(select(TaskException).where(TaskException.organization_id == org_id))
    exceptions = []
    for exc in exc_res.scalars().all():
        # normalize applicable_groups to a string representation for the solver
        exceptions.append({
            "task": exc.task,
            "applicable_groups": ",".join(exc.applicable_groups),
            "allow_consecutive": exc.allow_consecutive
        })

    # Fetch Class Teachers
    ct_res = await db.execute(
        select(Resource.name, Resource.home_group)
        .where(Resource.organization_id == org_id)
        .where(Resource.home_group.is_not(None))
    )
    class_teachers = {name: home_group for name, home_group in ct_res.all()}

    # 2. Run solver
    solver_status, raw_schedule, obj_val = solve_timetable(
        days_list=days_list,
        slots_count=slots_count,
        allocations=allocations,
        availability=availability,
        exceptions=exceptions,
        class_teachers=class_teachers
    )

    # 3. Post-Solve Validation
    violations = []
    if solver_status in ["OPTIMAL", "FEASIBLE"]:
        violations = validate_schedule_output(
            days_list=days_list,
            slots_count=slots_count,
            allocations=allocations,
            availability=availability,
            exceptions=exceptions,
            schedule=raw_schedule
        )

    # Create log entry
    log_status = "SUCCESS" if (solver_status in ["OPTIMAL", "FEASIBLE"] and not violations) else "FAILED"
    gen_log = ScheduleGenerationLog(
        organization_id=org_id,
        status=log_status,
        violations=violations if violations else None,
        solved_at=datetime.utcnow(),
        solver_status=solver_status
    )
    db.add(gen_log)

    if log_status == "FAILED":
        await db.commit()
        error_msg = f"Schedule generation failed. Status: {solver_status}."
        if violations:
            error_msg += f" Violations: {'; '.join(violations)}"
        return {"status": "failed", "detail": error_msg, "violations": violations}

    # Save schedule
    try:
        # Clear existing schedule
        await db.execute(delete(Schedule).where(Schedule.organization_id == org_id))

        # Re-query resources & groups to map names back to UUIDs
        res_res = await db.execute(select(Resource).where(Resource.organization_id == org_id))
        res_map = {r.name: r.id for r in res_res.scalars().all()}
        grp_res = await db.execute(select(Group).where(Group.organization_id == org_id))
        grp_map = {g.name: g.id for g in grp_res.scalars().all()}

        for item in raw_schedule:
            sch = Schedule(
                organization_id=org_id,
                day=item["day"],
                slot=item["slot"],
                resource_id=res_map[item["resource_name"]],
                group_id=grp_map[item["group_name"]],
                task=item["task"]
            )
            db.add(sch)

        await db.commit()
        
        # Notify WebSocket clients
        await manager.broadcast(str(org_id), {"type": "schedule_updated"})
        return {"status": "success", "message": "Timetable generated and verified successfully."}
        
    except Exception as e:
        await db.rollback()
        logger.error("schedule_save_failed", error=str(e))
        raise HTTPException(status_code=500, detail=f"Failed to commit schedule: {str(e)}")

@router.get("/organizations/{org_id}/schedule")
async def get_schedule(org_id: UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Schedule, Resource.name, Group.name).join(Resource).join(Group).where(Schedule.organization_id == org_id)
    )
    schedule_list = []
    for sch, r_name, g_name in result.all():
        schedule_list.append({
            "id": str(sch.id),
            "day": sch.day,
            "slot": sch.slot,
            "resource_id": str(sch.resource_id),
            "resource_name": r_name,
            "group_id": str(sch.group_id),
            "group_name": g_name,
            "task": sch.task
        })
    return schedule_list

@router.get("/organizations/{org_id}/schedule/resource/{resource_id}")
async def get_resource_schedule(org_id: UUID, resource_id: UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Schedule, Resource.name, Group.name)
        .join(Resource)
        .join(Group)
        .where(Schedule.organization_id == org_id)
        .where(Schedule.resource_id == resource_id)
    )
    schedule_list = []
    for sch, r_name, g_name in result.all():
        schedule_list.append({
            "id": str(sch.id),
            "day": sch.day,
            "slot": sch.slot,
            "resource_id": str(sch.resource_id),
            "resource_name": r_name,
            "group_id": str(sch.group_id),
            "group_name": g_name,
            "task": sch.task
        })
    return schedule_list

@router.get("/organizations/{org_id}/schedule/group/{group_id}")
async def get_group_schedule(org_id: UUID, group_id: UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Schedule, Resource.name, Group.name)
        .join(Resource)
        .join(Group)
        .where(Schedule.organization_id == org_id)
        .where(Schedule.group_id == group_id)
    )
    schedule_list = []
    for sch, r_name, g_name in result.all():
        schedule_list.append({
            "id": str(sch.id),
            "day": sch.day,
            "slot": sch.slot,
            "resource_id": str(sch.resource_id),
            "resource_name": r_name,
            "group_id": str(sch.group_id),
            "group_name": g_name,
            "task": sch.task
        })
    return schedule_list

@router.post("/organizations/{org_id}/schedule/edit")
async def edit_schedule(org_id: UUID, payload: ScheduleEdit, db: AsyncSession = Depends(get_db)):
    # Manual override edit
    org = await db.get(Organization, org_id)
    if not org or not org.config:
        raise HTTPException(status_code=404, detail="Organization or configuration not found.")

    days_list = org.config.get("day_names", [])
    slots_count = org.config.get("slots_per_day", 0)

    # 1. Fetch current schedule from DB
    curr_res = await db.execute(
        select(Schedule, Resource.name, Group.name).join(Resource).join(Group).where(Schedule.organization_id == org_id)
    )
    
    schedule_items = []
    target_sch = None
    for sch, r_name, g_name in curr_res.all():
        item = {
            "id": sch.id,
            "day": sch.day,
            "slot": sch.slot,
            "resource_id": sch.resource_id,
            "resource_name": r_name,
            "group_id": sch.group_id,
            "group_name": g_name,
            "task": sch.task
        }
        schedule_items.append(item)
        
        # Match target slot we want to edit
        if sch.day == payload.day and sch.slot == payload.slot and sch.group_id == payload.group_id:
            target_sch = sch

    # Fetch resource details for names
    res_details = await db.get(Resource, payload.resource_id)
    grp_details = await db.get(Group, payload.group_id)
    if not res_details or not grp_details:
         raise HTTPException(status_code=404, detail="Resource or Group not found.")

    # 2. Construct the "proposed" schedule mapping
    proposed_schedule = []
    found_replacement = False
    
    for item in schedule_items:
        if item["day"] == payload.day and item["slot"] == payload.slot and item["group_id"] == payload.group_id:
            # Replace target slot with new values
            proposed_schedule.append({
                "day": payload.day,
                "slot": payload.slot,
                "resource_name": res_details.name,
                "group_name": grp_details.name,
                "task": payload.task
            })
            found_replacement = True
        else:
            proposed_schedule.append({
                "day": item["day"],
                "slot": item["slot"],
                "resource_name": item["resource_name"],
                "group_name": item["group_name"],
                "task": item["task"]
            })

    if not found_replacement:
        # If the cell was empty, just insert the new record
        proposed_schedule.append({
            "day": payload.day,
            "slot": payload.slot,
            "resource_name": res_details.name,
            "group_name": grp_details.name,
            "task": payload.task
        })

    # Fetch solver inputs to re-validate
    alloc_res = await db.execute(
        select(Allocation, Resource.name, Group.name).join(Resource).join(Group).where(Allocation.organization_id == org_id)
    )
    allocations = [{"resource_name": rn, "group_name": gn, "task": a.task, "weekly_count": a.weekly_count} for a, rn, gn in alloc_res.all()]

    avail_res = await db.execute(
        select(Availability, Resource.name).join(Resource).where(Availability.organization_id == org_id)
    )
    availability = {rn: {"allowed_days": av.allowed_days, "allowed_slots": av.allowed_slots, "max_per_day": av.max_per_day} for av, rn in avail_res.all()}

    exc_res = await db.execute(select(TaskException).where(TaskException.organization_id == org_id))
    exceptions = [{"task": e.task, "applicable_groups": ",".join(e.applicable_groups), "allow_consecutive": e.allow_consecutive} for e in exc_res.scalars().all()]

    # 3. Post-Solve Validation
    violations = validate_schedule_output(
        days_list=days_list,
        slots_count=slots_count,
        allocations=allocations,
        availability=availability,
        exceptions=exceptions,
        schedule=proposed_schedule
    )

    if violations:
        raise HTTPException(
            status_code=400,
            detail={"message": "Manual edit violates constraints.", "violations": violations}
        )

    # 4. Save the manual edit to DB
    if target_sch:
        # Update existing
        target_sch.resource_id = payload.resource_id
        target_sch.task = payload.task
        db.add(target_sch)
    else:
        # Insert new
        new_sch = Schedule(
            organization_id=org_id,
            day=payload.day,
            slot=payload.slot,
            group_id=payload.group_id,
            resource_id=payload.resource_id,
            task=payload.task
        )
        db.add(new_sch)

    await db.commit()
    # Notify WS clients
    await manager.broadcast(str(org_id), {"type": "schedule_updated"})
    return {"status": "success", "message": "Manual edit saved and verified successfully."}

@router.get("/organizations/{org_id}/meta")
async def get_organization_meta(org_id: UUID, db: AsyncSession = Depends(get_db)):
    org = await db.get(Organization, org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    
    # Query resources and groups
    resources = await db.execute(select(Resource).where(Resource.organization_id == org_id))
    groups = await db.execute(select(Group).where(Group.organization_id == org_id))

    return {
        "days": org.config.get("day_names", []) if org.config else [],
        "slots_count": org.config.get("slots_per_day", 0) if org.config else 0,
        "resources": [{"id": str(r.id), "name": r.name} for r in resources.scalars().all()],
        "groups": [{"id": str(g.id), "name": g.name} for g in groups.scalars().all()]
    }

@router.get("/organizations/{org_id}/logs", response_model=List[ScheduleGenerationLogOut])
async def get_solve_logs(org_id: UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(ScheduleGenerationLog)
        .where(ScheduleGenerationLog.organization_id == org_id)
        .order_by(ScheduleGenerationLog.solved_at.desc())
    )
    return list(result.scalars().all())

@router.websocket("/organizations/{org_id}/ws")
async def websocket_endpoint(websocket: WebSocket, org_id: str):
    await manager.connect(org_id, websocket)
    try:
        while True:
            # Keep connection alive
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(org_id, websocket)
