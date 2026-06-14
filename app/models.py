from datetime import datetime
from enum import Enum
from typing import Optional, List
import base64
from sqlalchemy import Column, Integer, String, Text, Boolean, Float, DateTime, ForeignKey, JSON
from sqlalchemy.orm import relationship
from app.database import Base

class UserRole(str, Enum):
    USER = "user"
    ADMIN = "admin"

class PredictionStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"

# SQLAlchemy User Model
class User(Base):
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), unique=True, index=True, nullable=False)
    username = Column(String(255), unique=True, index=True, nullable=False)
    hashed_password = Column(String(255), nullable=False)
    role = Column(String(50), default=UserRole.USER.value, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    subscription_plan = Column(String(50), default="free", nullable=False)
    subscription_status = Column(String(50), default="inactive", nullable=False)
    subscription_cycle = Column(String(50), default="monthly", nullable=False)
    stripe_customer_id = Column(String(255), nullable=True, index=True)
    stripe_subscription_id = Column(String(255), nullable=True, index=True)
    stripe_price_id = Column(String(255), nullable=True)
    subscription_current_period_end = Column(DateTime, nullable=True)
    subscription_updated_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    # Relationship
    videos = relationship("Video", back_populates="user", cascade="all, delete-orphan")
    
    def __repr__(self):
        return f"<User(id={self.id}, email={self.email}, username={self.username})>"

# SQLAlchemy Video Model
class Video(Base):
    __tablename__ = "videos"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    filename = Column(String(255), nullable=False)
    original_filename = Column(String(255), nullable=False)
    file_path = Column(String(512), nullable=False)
    cloud_url = Column(String(1024), nullable=True)  # Cloudinary URL for persistent storage
    file_size = Column(Integer, nullable=False)
    file_type = Column(String(50), default="video", nullable=False)
    
    status = Column(String(50), default=PredictionStatus.PENDING.value, nullable=False, index=True)
    is_deepfake = Column(Boolean, nullable=True)
    confidence_score = Column(Float, nullable=True)
    prediction_details = Column(JSON, nullable=True)  # Store analysis results as JSON
    
    uploaded_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    processed_at = Column(DateTime, nullable=True)
    
    thumbnail_base64 = Column(Text, nullable=True)  # Base64 encoded thumbnail
    annotated_frames_base64 = Column(JSON, default=list, nullable=True)  # List of base64 frames
    
    # Relationships
    user = relationship("User", back_populates="videos")
    frames = relationship("Frame", back_populates="video", cascade="all, delete-orphan")
    
    @property
    def frame_analysis(self):
        """Construct frame analysis summary from related frames"""
        if not self.frames:
            return None
        
        fake_frames = sum(1 for f in self.frames if f.is_fake)
        real_frames = sum(1 for f in self.frames if f.is_fake is False)
        suspicious_frames = sum(1 for f in self.frames if f.is_suspicious)
        
        return {
            "total_frames": len(self.frames),
            "fake_frames": fake_frames,
            "real_frames": real_frames,
            "suspicious_frames": suspicious_frames,
            "frame_details": [
                {
                    "frame_number": f.frame_number,
                    "timestamp": f.timestamp,
                    "is_fake": f.is_fake,
                    "is_suspicious": f.is_suspicious,
                    "confidence_score": f.confidence_score,
                    "image_base64": f.image_base64,
                    "thumbnail_base64": f.thumbnail_base64,
                    "analysis_details": f.analysis_details
                }
                for f in self.frames
            ]
        }
    
    def __repr__(self):
        return f"<Video(id={self.id}, user_id={self.user_id}, filename={self.filename})>"

# SQLAlchemy Frame Model - Stores individual frame analysis
class Frame(Base):
    __tablename__ = "frames"
    
    id = Column(Integer, primary_key=True, index=True)
    video_id = Column(Integer, ForeignKey("videos.id"), nullable=False, index=True)
    frame_number = Column(Integer, nullable=False)  # 0-based index
    timestamp = Column(Float, nullable=True)  # Timestamp in seconds
    
    # Frame classification
    is_fake = Column(Boolean, nullable=True)  # True if deepfake detected
    is_suspicious = Column(Boolean, nullable=True)  # True if suspicious
    confidence_score = Column(Float, nullable=True)  # Confidence 0-100
    
    # Image storage
    image_base64 = Column(Text, nullable=True)  # Base64 encoded frame image
    thumbnail_base64 = Column(Text, nullable=True)  # Smaller thumbnail
    
    # Analysis details
    analysis_details = Column(JSON, nullable=True)  # Additional analysis metadata
    
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    # Relationship
    video = relationship("Video", back_populates="frames")
    
    def __repr__(self):
        return f"<Frame(id={self.id}, video_id={self.video_id}, frame={self.frame_number})>"

# Helper functions for Base64 encoding
def encode_image_to_base64(image_bytes: bytes) -> str:
    """Convert image bytes to Base64 string"""
    return base64.b64encode(image_bytes).decode('utf-8')

def decode_base64_to_image(base64_string: str) -> bytes:
    """Convert Base64 string back to image bytes"""
    return base64.b64decode(base64_string.encode('utf-8'))
