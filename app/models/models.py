import uuid
from datetime import datetime
from sqlalchemy import Column, String, Integer, Boolean, ForeignKey, UniqueConstraint, DateTime
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()

class Organization(Base):
    __tablename__ = "organizations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String, nullable=False)
    subdomain = Column(String, unique=True, nullable=False)
    config = Column(JSONB, nullable=True) # e.g. {"days_list": ["Mon", "Tue"], "slots_count": 8}
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    resources = relationship("Resource", back_populates="organization", cascade="all, delete-orphan")
    groups = relationship("Group", back_populates="organization", cascade="all, delete-orphan")
    allocations = relationship("Allocation", back_populates="organization", cascade="all, delete-orphan")
    availability = relationship("Availability", back_populates="organization", cascade="all, delete-orphan")
    task_exceptions = relationship("TaskException", back_populates="organization", cascade="all, delete-orphan")
    schedules = relationship("Schedule", back_populates="organization", cascade="all, delete-orphan")
    logs = relationship("ScheduleGenerationLog", back_populates="organization", cascade="all, delete-orphan")

class Resource(Base):
    __tablename__ = "resources"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    name = Column(String, nullable=False)
    home_group = Column(String, nullable=True) # e.g. class teacher of

    organization = relationship("Organization", back_populates="resources")
    allocations = relationship("Allocation", back_populates="resource", cascade="all, delete-orphan")
    availability = relationship("Availability", back_populates="resource", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("organization_id", "name", name="uq_organization_resource_name"),
    )

class Group(Base):
    __tablename__ = "groups"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    name = Column(String, nullable=False)

    organization = relationship("Organization", back_populates="groups")
    allocations = relationship("Allocation", back_populates="group", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("organization_id", "name", name="uq_organization_group_name"),
    )

class Allocation(Base):
    __tablename__ = "allocations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    resource_id = Column(UUID(as_uuid=True), ForeignKey("resources.id", ondelete="CASCADE"), nullable=False)
    group_id = Column(UUID(as_uuid=True), ForeignKey("groups.id", ondelete="CASCADE"), nullable=False)
    task = Column(String, nullable=False) # e.g. Subject
    weekly_count = Column(Integer, nullable=False)

    organization = relationship("Organization", back_populates="allocations")
    resource = relationship("Resource", back_populates="allocations")
    group = relationship("Group", back_populates="allocations")

class Availability(Base):
    __tablename__ = "availability"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    resource_id = Column(UUID(as_uuid=True), ForeignKey("resources.id", ondelete="CASCADE"), nullable=False)
    allowed_days = Column(JSONB, nullable=False) # e.g. ["Mon", "Tue"]
    allowed_slots = Column(JSONB, nullable=False) # e.g. [1, 2, 3, 4]
    max_per_day = Column(Integer, nullable=False)

    organization = relationship("Organization", back_populates="availability")
    resource = relationship("Resource", back_populates="availability")

class TaskException(Base):
    __tablename__ = "task_exceptions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    task = Column(String, nullable=False)
    applicable_groups = Column(JSONB, nullable=False) # list of group names, or ["*"] for all
    allow_consecutive = Column(Boolean, nullable=False, default=False)

    organization = relationship("Organization", back_populates="task_exceptions")

class Schedule(Base):
    __tablename__ = "schedule"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    day = Column(Integer, nullable=False) # 0-indexed day
    slot = Column(Integer, nullable=False) # 0-indexed slot
    group_id = Column(UUID(as_uuid=True), ForeignKey("groups.id", ondelete="CASCADE"), nullable=False)
    resource_id = Column(UUID(as_uuid=True), ForeignKey("resources.id", ondelete="CASCADE"), nullable=False)
    task = Column(String, nullable=False)

    organization = relationship("Organization", back_populates="schedules")
    group = relationship("Group")
    resource = relationship("Resource")

    __table_args__ = (
        UniqueConstraint("organization_id", "day", "slot", "group_id", name="uq_schedule_group_slot"),
        UniqueConstraint("organization_id", "day", "slot", "resource_id", name="uq_schedule_resource_slot"),
    )

class ScheduleGenerationLog(Base):
    __tablename__ = "schedule_generation_log"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    status = Column(String, nullable=False) # "SUCCESS", "FAILED"
    violations = Column(JSONB, nullable=True) # list of verification errors
    solved_at = Column(DateTime, default=datetime.utcnow)
    solver_status = Column(String, nullable=False) # "OPTIMAL", "FEASIBLE", "INFEASIBLE", "UNKNOWN"

    organization = relationship("Organization", back_populates="logs")
