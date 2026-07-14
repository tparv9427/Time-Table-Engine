from pydantic import BaseModel, ConfigDict, Field
from uuid import UUID
from datetime import datetime
from typing import List, Optional

class OrganizationBase(BaseModel):
    name: str
    subdomain: str

class OrganizationCreate(OrganizationBase):
    pass

class OrganizationOut(OrganizationBase):
    id: UUID
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

class ResourceBase(BaseModel):
    name: str
    home_group: Optional[str] = None

class ResourceCreate(ResourceBase):
    pass

class ResourceOut(ResourceBase):
    id: UUID
    organization_id: UUID

    model_config = ConfigDict(from_attributes=True)

class GroupBase(BaseModel):
    name: str

class GroupCreate(GroupBase):
    pass

class GroupOut(GroupBase):
    id: UUID
    organization_id: UUID

    model_config = ConfigDict(from_attributes=True)

class AllocationBase(BaseModel):
    resource_id: UUID
    group_id: UUID
    task: str
    weekly_count: int

class AllocationCreate(AllocationBase):
    pass

class AllocationOut(AllocationBase):
    id: UUID
    organization_id: UUID

    model_config = ConfigDict(from_attributes=True)

class AvailabilityBase(BaseModel):
    resource_id: UUID
    allowed_days: List[str]
    allowed_slots: List[int]
    max_per_day: int

class AvailabilityCreate(AvailabilityBase):
    pass

class AvailabilityOut(AvailabilityBase):
    id: UUID
    organization_id: UUID

    model_config = ConfigDict(from_attributes=True)

class TaskExceptionBase(BaseModel):
    task: str
    applicable_groups: List[str]
    allow_consecutive: bool

class TaskExceptionCreate(TaskExceptionBase):
    pass

class TaskExceptionOut(TaskExceptionBase):
    id: UUID
    organization_id: UUID

    model_config = ConfigDict(from_attributes=True)

class ScheduleBase(BaseModel):
    day: int
    slot: int
    group_id: UUID
    resource_id: UUID
    task: str

class ScheduleCreate(ScheduleBase):
    pass

class ScheduleEdit(BaseModel):
    day: int
    slot: int
    group_id: UUID
    resource_id: UUID
    task: str

class ScheduleOut(ScheduleBase):
    id: UUID
    organization_id: UUID

    model_config = ConfigDict(from_attributes=True)

class ScheduleGenerationLogOut(BaseModel):
    id: UUID
    organization_id: UUID
    status: str
    violations: Optional[List[str]] = None
    solved_at: datetime
    solver_status: str

    model_config = ConfigDict(from_attributes=True)

class TeacherWizardInput(BaseModel):
    name: str
    subject: str
    weekly_count: int
    target_class: str
    allowed_days: Optional[List[str]] = None
    allowed_slots: Optional[List[int]] = None
    max_per_day: Optional[int] = None
    is_class_teacher_of: Optional[str] = None # holds class name or empty

class WizardSetupInput(BaseModel):
    org_name: str
    timetable_type: str = "school"
    working_days: List[str]
    slots_count: int
    classes: List[str]
    teachers: List[TeacherWizardInput]

