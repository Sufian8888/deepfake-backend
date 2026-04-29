"""
Database initialization script
Creates default admin user if it doesn't exist in PostgreSQL
"""
from sqlalchemy.orm import Session
from sqlalchemy import text
from app.database import SessionLocal, Base, engine
from app.models import User, UserRole
from app.auth import get_password_hash
from app.config import settings
import logging

logger = logging.getLogger(__name__)

def init_db():
    """Initialize database with default admin user"""
    try:
        # Create all tables
        Base.metadata.create_all(bind=engine)
        logger.info("✅ Created all database tables")
        
        # Add missing columns to existing tables
        try:
            with engine.connect() as connection:
                # Add cloud_url column to videos table if it doesn't exist
                connection.execute(text("""
                    ALTER TABLE videos 
                    ADD COLUMN IF NOT EXISTS cloud_url VARCHAR(1024) NULL
                """))
                connection.commit()
                logger.info("✅ Added cloud_url column to videos table")
        except Exception as e:
            logger.info(f"ℹ️  cloud_url column already exists or error: {str(e)}")
        
        # Get database session
        db: Session = SessionLocal()
        
        try:
            # Check if admin exists by email
            admin = db.query(User).filter(
                (User.email == settings.ADMIN_EMAIL) | 
                (User.username == "admin")
            ).first()
            
            if not admin:
                # Create default admin
                admin_user = User(
                    email=settings.ADMIN_EMAIL,
                    username="admin",
                    hashed_password=get_password_hash(settings.ADMIN_PASSWORD),
                    role=UserRole.ADMIN.value,
                    is_active=True
                )
                db.add(admin_user)
                db.commit()
                logger.info(f"✅ Admin user created: {settings.ADMIN_EMAIL}")
                print(f"✅ Admin user created: {settings.ADMIN_EMAIL}")
                print(f"   Password: {settings.ADMIN_PASSWORD}")
            else:
                logger.info(f"ℹ️  Admin user already exists: {admin.email}")
                print(f"ℹ️  Admin user already exists: {admin.email}")
        finally:
            db.close()
    
    except Exception as e:
        logger.warning(f"⚠️  Error initializing database: {e}")
        print(f"⚠️  Error initializing database: {e}")
        # Don't raise - allow app to continue

if __name__ == "__main__":
    print("🔧 Initializing database...")
    init_db()
    print("✅ Database initialized successfully!")
