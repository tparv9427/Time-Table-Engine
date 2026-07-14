import os

class Settings:
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL", 
        "postgresql+asyncpg://postgres:postgres@localhost:5432/timetable_db"
    )
    ENVIRONMENT: str = os.getenv("ENVIRONMENT", "development")

settings = Settings()
