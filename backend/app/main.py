from fastapi import FastAPI, Depends, Form, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from contextlib import asynccontextmanager
from sqlalchemy.orm import Session
import os
from backend.app.config import settings
from backend.app.database import engine, Base, SessionLocal, get_db
from backend.app.routes import meta_webhook, daraja_webhook, dashboard
from backend.app.services.scheduler import start_scheduler
from backend.app.services.chat_bot import ChatBotService, USER_SESSIONS
from backend.app.models import PlatformType, User, Deal, DealStatus
import logging
import time

LAST_PROCESSED_MESSAGES = {}

# Configure Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("main")

DB_STATUS = {"connected": False, "error": None}

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Create tables in Database with retry
    logger.info("Initializing database tables...")
    db_connected = False
    for attempt in range(1, 6):
        try:
            Base.metadata.create_all(bind=engine)
            db_connected = True
            DB_STATUS["connected"] = True
            DB_STATUS["error"] = None
            logger.info("Database tables initialized successfully.")
            break
        except Exception as db_err:
            DB_STATUS["error"] = str(db_err)
            logger.warning(f"Database connection attempt {attempt}/5 failed: {db_err}")
            if attempt < 5:
                time.sleep(2)
            else:
                logger.error(
                    "CRITICAL: Failed to connect to database after 5 attempts. "
                    "If using Render PostgreSQL, ensure you are using the EXTERNAL Database URL (not the internal .render hostname). "
                    "Server will continue booting to serve healthchecks and diagnostics."
                )

    if db_connected:
        # Auto-migrate SQLite/PostgreSQL users table for new fields
        try:
            from sqlalchemy import inspect
            from sqlalchemy import text
            inspector = inspect(engine)
            if inspector.has_table('users'):
                columns = [col['name'] for col in inspector.get_columns('users')]
                with engine.begin() as conn:
                    if 'name' not in columns:
                        conn.execute(text("ALTER TABLE users ADD COLUMN name VARCHAR(100)"))
                        logger.info("Added name column to users table.")
                    if 'recovery_email_or_phone' not in columns:
                        conn.execute(text("ALTER TABLE users ADD COLUMN recovery_email_or_phone VARCHAR(100)"))
                        logger.info("Added recovery_email_or_phone column to users table.")
                    if 'location' not in columns:
                        conn.execute(text("ALTER TABLE users ADD COLUMN location VARCHAR(100)"))
                        logger.info("Added location column to users table.")
                    if 'consent_accepted_at' not in columns:
                        conn.execute(text("ALTER TABLE users ADD COLUMN consent_accepted_at DATETIME"))
                        logger.info("Added consent_accepted_at column to users table.")
                    if 'payout_mpesa_number' not in columns:
                        conn.execute(text("ALTER TABLE users ADD COLUMN payout_mpesa_number VARCHAR(20)"))
                        logger.info("Added payout_mpesa_number column to users table.")
                    if 'name_verified' not in columns:
                        conn.execute(text("ALTER TABLE users ADD COLUMN name_verified BOOLEAN DEFAULT 0"))
                        logger.info("Added name_verified column to users table.")
                    if 'password_hash' not in columns:
                        conn.execute(text("ALTER TABLE users ADD COLUMN password_hash VARCHAR(100)"))
                        logger.info("Added password_hash column to users table.")
                    if 'session_token' not in columns:
                        conn.execute(text("ALTER TABLE users ADD COLUMN session_token VARCHAR(100)"))
                        logger.info("Added session_token column to users table.")
            
            # Auto-migrate disputes table for new fields
            if inspector.has_table('disputes'):
                columns_disputes = [col['name'] for col in inspector.get_columns('disputes')]
                with engine.begin() as conn:
                    if 'resolution_statement' not in columns_disputes:
                        conn.execute(text("ALTER TABLE disputes ADD COLUMN resolution_statement TEXT"))
                        logger.info("Added resolution_statement column to disputes table.")
                    if 'first_instance_outcome' not in columns_disputes:
                        conn.execute(text("ALTER TABLE disputes ADD COLUMN first_instance_outcome VARCHAR(50)"))
                        logger.info("Added first_instance_outcome column to disputes table.")
                    if 'first_instance_statement' not in columns_disputes:
                        conn.execute(text("ALTER TABLE disputes ADD COLUMN first_instance_statement TEXT"))
                        logger.info("Added first_instance_statement column to disputes table.")

            # Auto-migrate deals table for new fields (short_code)
            if inspector.has_table('deals'):
                columns_deals = [col['name'] for col in inspector.get_columns('deals')]
                with engine.begin() as conn:
                    if 'short_code' not in columns_deals:
                        conn.execute(text("ALTER TABLE deals ADD COLUMN short_code VARCHAR(10)"))
                        logger.info("Added short_code column to deals table.")

            # Auto-migrate payments table for new fields (reversal_ref, is_reversed)
            if inspector.has_table('payments'):
                columns_payments = [col['name'] for col in inspector.get_columns('payments')]
                with engine.begin() as conn:
                    if 'reversal_ref' not in columns_payments:
                        conn.execute(text("ALTER TABLE payments ADD COLUMN reversal_ref VARCHAR(100)"))
                        logger.info("Added reversal_ref column to payments table.")
                    if 'is_reversed' not in columns_payments:
                        conn.execute(text("ALTER TABLE payments ADD COLUMN is_reversed BOOLEAN DEFAULT 0"))
                        logger.info("Added is_reversed column to payments table.")

            # Backfill any existing deals without short_code
            db_bf = SessionLocal()
            try:
                from backend.app.models import Deal, generate_short_code
                empty_deals = db_bf.query(Deal).filter((Deal.short_code == None) | (Deal.short_code == "")).all()
                for d in empty_deals:
                    d.short_code = generate_short_code()
                if empty_deals:
                    db_bf.commit()
                    logger.info(f"Backfilled short_code for {len(empty_deals)} existing deals.")
            except Exception as bf_err:
                logger.error(f"Error backfilling deal short_codes: {bf_err}")
            finally:
                db_bf.close()

            # Hydrate persistent user sessions from database
            try:
                from backend.app.services.chat_bot import USER_SESSIONS
                USER_SESSIONS.load_all(SessionLocal)
            except Exception as sess_err:
                logger.error(f"Error hydrating persistent sessions: {sess_err}")
        except Exception as migration_err:
            logger.error(f"Error running database table migration: {migration_err}")
        
        # Seed staff users
        db = SessionLocal()
        try:
            from backend.app.models import User, UserRole
            import hashlib
            staff = [
                ("MOD_1", UserRole.MODERATOR),
                ("ARB_1", UserRole.ARBITRATOR),
                ("ADMIN_1", UserRole.ADMIN)
            ]
            for phone, role in staff:
                exists = db.query(User).filter(User.phone_or_handle == phone).first()
                pwd = "moderator123" if role == UserRole.MODERATOR else ("arbitrator123" if role == UserRole.ARBITRATOR else "admin123")
                expected_hash = hashlib.sha256(pwd.encode('utf-8')).hexdigest()
                
                if exists:
                    # Update role and password if needed
                    if not exists.password_hash or exists.role != role:
                        exists.password_hash = expected_hash
                        exists.role = role
                        logger.info(f"Updated staff user credentials: {phone}")
                else:
                    user = User(
                        phone_or_handle=phone,
                        role=role,
                        platform=PlatformType.WHATSAPP,
                        trust_score=100.0,
                        password_hash=expected_hash
                    )
                    db.add(user)
                    logger.info(f"Seeded staff user: {phone} as {role.value}")
            # Clean up historical test disputes so scheduler doesn't loop on old tests
            from backend.app.models import Dispute
            stale_disputes = db.query(Dispute).filter(Dispute.resolved_at != None, Dispute.filer_satisfied == None).all()
            for sd in stale_disputes:
                sd.filer_satisfied = True
            db.commit()
        except Exception as seed_err:
            logger.error(f"Error during startup data initialization: {seed_err}")
        finally:
            db.close()
            
        # Start APScheduler tasks
        logger.info("Initializing background scheduler...")
        start_scheduler(SessionLocal)
    
    yield
    # Shutdown logic (if any)
    logger.info("Shutting down services...")

app = FastAPI(
    title=settings.PROJECT_NAME,
    description="Secure Chat-Native Escrow Backend",
    version="1.0.0",
    lifespan=lifespan
)

# CORS Configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # Open for ease of testing in Vite/React
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def normalize_path_middleware(request: Request, call_next):
    if "//" in request.url.path:
        cleaned_path = "/" + "/".join([part for part in request.url.path.split("/") if part])
        request.scope["path"] = cleaned_path
    return await call_next(request)

# Attach Webhook and Dashboard Routers
app.include_router(meta_webhook.router, prefix="/api")
app.include_router(daraja_webhook.router, prefix="/api")
app.include_router(dashboard.router, prefix="/api")

# ----------------------------------------------------
# Container & Health Check Endpoints (Coolify / Docker)
# ----------------------------------------------------
@app.get("/health", tags=["Health"])
@app.get("/api/health", tags=["Health"])
def health_check():
    return {
        "status": "ok",
        "app": settings.PROJECT_NAME,
        "mode": "simulation" if settings.SIMULATION_MODE else "production",
        "database": "connected" if DB_STATUS["connected"] else "error",
        "db_error": DB_STATUS["error"]
    }

@app.get("/api", tags=["API Root"])
def api_root():
    return {
        "status": "online",
        "app": settings.PROJECT_NAME,
        "mode": "simulation" if settings.SIMULATION_MODE else "production"
    }

# ----------------------------------------------------
# DIRECT SANDBOX CHAT INTERACTION ENDPOINT
# ----------------------------------------------------

@app.post("/api/dialogue")
def dialogue_chat_simulator(
    phone_or_handle: str = Form(...),
    message: str = Form(...),
    platform: str = Form("whatsapp"),
    media_url: str = Form(None),
    db: Session = Depends(get_db)
):
    """
    Direct endpoint for Sandbox simulator.
    Receives chat messages directly from the React interface, Feeds it to the bot,
    and returns the bot's raw response immediately (in addition to logging).
    """
    # 1. Idempotency safety net (2-second window)
    now = time.time()
    last_msg = LAST_PROCESSED_MESSAGES.get(phone_or_handle)
    if last_msg:
        last_time, last_text, last_reply_data = last_msg
        if now - last_time < 2.0 and last_text == message:
            logger.info(f"Duplicate message '{message}' ignored (idempotency key match for {phone_or_handle})")
            return last_reply_data

    try:
        plat_enum = PlatformType(platform.lower())
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid platform. Must be whatsapp, messenger, or instagram.")

    # 2. Process message exactly ONCE
    bot_reply = ChatBotService.process_message(
        db=db,
        platform=plat_enum,
        phone_or_handle=phone_or_handle,
        text=message,
        media_url=media_url
    )

    # 3. Retrieve active deal_id from session *after* execution
    session_after = USER_SESSIONS.get(phone_or_handle, {})
    deal_id = session_after.get("deal_id")
    if not deal_id:
        user_db = db.query(User).filter(User.phone_or_handle == phone_or_handle).first()
        if user_db:
            latest_deal = db.query(Deal).filter(
                (Deal.seller_id == user_db.id) | (Deal.buyer_id == user_db.id)
            ).order_by(Deal.created_at.desc()).first()
            if latest_deal and latest_deal.status not in [DealStatus.COMPLETED, DealStatus.REFUNDED, DealStatus.CANCELLED]:
                deal_id = latest_deal.id

    # Save Bot reply to chat logs using the computed deal_id
    if deal_id and bot_reply:
        from datetime import datetime, UTC
        from backend.app.models import ChatLog
        from backend.app.services.meta_service import MetaService
        
        bot_id = MetaService._ensure_bot_user_exists(db)
        
        # Check if the last log is from the bot and matches content to avoid duplicates
        last_log = db.query(ChatLog).filter(ChatLog.deal_id == deal_id).order_by(ChatLog.timestamp.desc()).first()
        if not last_log or last_log.message_content != bot_reply:
            chat_log = ChatLog(
                deal_id=deal_id,
                sender_id=bot_id,
                message_content=bot_reply,
                timestamp=datetime.now(UTC).replace(tzinfo=None)
            )
            db.add(chat_log)
            db.commit()
    
    buttons = []
    if bot_reply:
        from backend.app.services.meta_service import MetaService
        buttons = MetaService.get_interactive_options_for_text(bot_reply)

    reply_data = {
        "user": phone_or_handle,
        "message": message,
        "reply": bot_reply,
        "buttons": buttons,
        "session_state": USER_SESSIONS.get(phone_or_handle, {}).get("state"),
        "deal_id": USER_SESSIONS.get(phone_or_handle, {}).get("deal_id")
    }
    
    # Cache for idempotency checks
    LAST_PROCESSED_MESSAGES[phone_or_handle] = (now, message, reply_data)
    
    return reply_data

@app.get("/api/users/{user_identifier}/profile")
def get_user_public_profile(user_identifier: str, db: Session = Depends(get_db)):
    """
    Expose public profile details (handle, stats, badge) by user ID or phone/handle.
    """
    from backend.app.services.rating_service import RatingService
    
    user = db.query(User).filter(User.id == user_identifier).first()
    if not user:
        user = db.query(User).filter(User.phone_or_handle == user_identifier).first()
        
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
        
    summary = RatingService.get_profile_summary(db, user)
    return summary

# ----------------------------------------------------
# Frontend SPA Static Serving (Production / Container)
# ----------------------------------------------------
frontend_dist = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "frontend", "dist"))
if os.path.exists(frontend_dist) and os.path.isfile(os.path.join(frontend_dist, "index.html")):
    assets_dir = os.path.join(frontend_dist, "assets")
    if os.path.exists(assets_dir):
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    @app.get("/")
    async def serve_root():
        return FileResponse(os.path.join(frontend_dist, "index.html"))

    @app.get("/{full_path:path}")
    async def serve_spa_frontend(full_path: str):
        # Do not intercept API, health, docs, or openapi schema
        if full_path.startswith("api") or full_path.startswith("health") or full_path.startswith("docs") or full_path.startswith("openapi.json"):
            raise HTTPException(status_code=404, detail="Not Found")
        
        file_path = os.path.join(frontend_dist, full_path)
        if full_path and os.path.isfile(file_path):
            return FileResponse(file_path)
        return FileResponse(os.path.join(frontend_dist, "index.html"))
else:
    @app.get("/")
    def read_root():
        return {
            "status": "online",
            "app": settings.PROJECT_NAME,
            "mode": "simulation" if settings.SIMULATION_MODE else "production"
        }
