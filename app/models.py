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
    
    # Relationship
    user = relationship("User", back_populates="videos")
    
    def __repr__(self):
        return f"<Video(id={self.id}, user_id={self.user_id}, filename={self.filename})>"

# Helper functions for Base64 encoding
def encode_image_to_base64(image_bytes: bytes) -> str:
    """Convert image bytes to Base64 string"""
    return base64.b64encode(image_bytes).decode('utf-8')

def decode_base64_to_image(base64_string: str) -> bytes:
    """Convert Base64 string back to image bytes"""
    return base64.b64decode(base64_string.encode('utf-8'))
