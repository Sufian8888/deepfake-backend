from pydantic import BaseModel, EmailStr, Field
from typing import Optional, List
from datetime import datetime
from enum import Enum

# Enums
class UserRole(str, Enum):
    USER = "user"
    ADMIN = "admin"

class PredictionStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"

# User Schemas
class UserBase(BaseModel):
    email: EmailStr
    username: str

class UserCreate(BaseModel):
    email: EmailStr
    full_name: str
    password: str = Field(..., min_length=6)

class UserLogin(BaseModel):
    username: str  # Can be email or username
    password: str

class UserResponse(UserBase):
    id: int
    role: UserRole
    is_active: bool
    created_at: datetime
    
    class Config:
        from_attributes = True

# Token Schemas
class Token(BaseModel):
    access_token: str
    token_type: str
    user: UserResponse

class TokenData(BaseModel):
    email: Optional[str] = None

# Video Schemas
class VideoBase(BaseModel):
    original_filename: str
    file_type: str

class VideoResponse(BaseModel):
    id: int
    filename: str
    original_filename: str
    file_size: int
    file_type: str
    status: PredictionStatus
    is_deepfake: Optional[bool]
    confidence_score: Optional[float]
    prediction_details: Optional[str]
    cloud_url: Optional[str]
    uploaded_at: datetime
    processed_at: Optional[datetime]
    
    class Config:
        from_attributes = True

# Prediction Schemas
class PredictionResult(BaseModel):
    is_deepfake: bool
    confidence_score: float
    analysis_details: dict
    suggestions: List[str]

# Dashboard Schemas
class DashboardStats(BaseModel):
    total_videos: int
    total_predictions: int
    deepfakes_found: int
    genuine_videos: int
    pending_analyses: int
    success_rate: float = 0.0

# Admin Schemas
class AdminUserUpdate(BaseModel):
    is_active: Optional[bool] = None
    role: Optional[UserRole] = None

class AdminStats(BaseModel):
    total_users: int
    total_videos: int
    total_deepfakes: int
    active_users: int
    recent_users: List[UserResponse]
