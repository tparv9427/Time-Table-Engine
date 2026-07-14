from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
import structlog

from app.core.database import engine
from app.models.models import Base
from app.api.endpoints import router

logger = structlog.get_logger()

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize Database Tables
    logger.info("db_initialization_started")
    async with engine.begin() as conn:
        # Create all tables if they do not exist
        await conn.run_sync(Base.metadata.create_all)
    logger.info("db_initialization_completed")
    yield
    # Cleanup / Shutdown logic
    await engine.dispose()
    logger.info("db_connections_cleaned")

app = FastAPI(
    title="Generalized CP-SAT Timetable Engine",
    description="Conflict-Free Weekly Scheduling Engine using Google OR-Tools CP-SAT",
    version="1.0.0",
    lifespan=lifespan
)

# Enable CORS for local testing
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include API Router
app.include_router(router, prefix="/api")

# Mount Static Frontend
# Mount at root / to serve index.html directly
app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")
