import os
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, declarative_base
from backend.app.config import settings

db_url = settings.DATABASE_URL
# Normalize legacy postgres:// scheme to postgresql://
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

# For SQLite, enable check_same_thread=False to support multiple threads
connect_args = {}
if db_url.startswith("sqlite"):
    connect_args["check_same_thread"] = False
    # Ensure database directory exists if custom path is configured
    db_clean_path = db_url.replace("sqlite:////", "/").replace("sqlite:///", "")
    if db_clean_path and not db_clean_path.startswith(":memory:"):
        db_dir = os.path.dirname(os.path.abspath(db_clean_path))
        if db_dir and not os.path.exists(db_dir):
            try:
                os.makedirs(db_dir, exist_ok=True)
            except Exception:
                pass

engine = create_engine(
    db_url,
    connect_args=connect_args,
    pool_pre_ping=True
)

# Enable SQLite features: foreign keys and WAL mode
@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    if db_url.startswith("sqlite"):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
