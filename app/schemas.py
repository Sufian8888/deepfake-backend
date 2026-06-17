from pydantic import BaseModel, EmailStr, Field, ConfigDict, field_serializer, model_validator
from typing import Optional, List
from datetime import datetime
from enum import Enum

# Enums
class UserRole(str, Enum):
    USER = "user"
    ADMIN = "admin"


class BillingPlan(str, Enum):
    FREE = "free"
    PRO = "pro"
    ENTERPRISE = "enterprise"


class BillingCycle(str, Enum):
    MONTHLY = "monthly"
    YEARLY = "yearly"

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


class UserProfileUpdate(BaseModel):
    full_name: str = Field(..., min_length=1, max_length=100)


class UserPasswordUpdate(BaseModel):
    current_password: str = Field(..., min_length=6)
    new_password: str = Field(..., min_length=6)


class SendOTPRequest(BaseModel):
    email: EmailStr


class VerifyOTPRequest(BaseModel):
    email: EmailStr
    otp: str = Field(..., min_length=6, max_length=6, pattern=r"^\d{6}$")


class ContactRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    email: EmailStr
    subject: str = Field(..., min_length=1, max_length=200)
    message: str = Field(..., min_length=1, max_length=5000)


class MessageResponse(BaseModel):
    message: str

class UserResponse(UserBase):
    id: int
    role: UserRole
    is_active: bool
    subscription_plan: BillingPlan = BillingPlan.FREE
    subscription_status: str = "inactive"
    subscription_cycle: BillingCycle = BillingCycle.MONTHLY
    stripe_customer_id: Optional[str] = None
    stripe_subscription_id: Optional[str] = None
    stripe_price_id: Optional[str] = None
    subscription_current_period_end: Optional[datetime] = None
    subscription_updated_at: Optional[datetime] = None
    created_at: datetime
    
    class Config:
        from_attributes = True


class BillingCheckoutRequest(BaseModel):
    plan: BillingPlan
    billing_cycle: BillingCycle = BillingCycle.MONTHLY


class BillingConfirmRequest(BaseModel):
    session_id: str


class BillingCheckoutResponse(BaseModel):
    url: str


class BillingPortalResponse(BaseModel):
    url: str


class BillingInfoResponse(BaseModel):
    subscription_plan: BillingPlan = BillingPlan.FREE
    subscription_status: str = "inactive"
    subscription_cycle: BillingCycle = BillingCycle.MONTHLY
    subscription_current_period_end: Optional[datetime] = None
    is_premium: bool = False

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

class FrameAnalysisDetail(BaseModel):
    """Individual frame analysis result"""
    model_config = ConfigDict(from_attributes=True)
    
    frame_number: int
    timestamp: Optional[float] = None
    is_fake: Optional[bool] = None
    is_suspicious: Optional[bool] = None
    confidence_score: Optional[float] = None
    image_base64: Optional[str] = None  # Base64 encoded frame
    thumbnail_base64: Optional[str] = None  # Smaller thumbnail
    analysis_details: Optional[dict] = None

class FrameAnalysisSummary(BaseModel):
    """Summary of frame-level analysis"""
    model_config = ConfigDict(from_attributes=True)
    
    total_frames: int
    fake_frames: int  # Count of frames detected as fake
    real_frames: int  # Count of frames detected as real
    suspicious_frames: int  # Count of suspicious frames
    frame_details: List[FrameAnalysisDetail] = []

class VideoResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, arbitrary_types_allowed=True)
    
    id: int
    filename: str
    original_filename: str
    file_size: int
    file_type: str
    status: PredictionStatus
    is_deepfake: Optional[bool] = None
    confidence_score: Optional[float] = None
    prediction_details: Optional[str] = None
    cloud_url: Optional[str] = None
    frame_analysis: Optional[FrameAnalysisSummary] = None
    uploaded_at: datetime
    processed_at: Optional[datetime] = None
    
    @model_validator(mode='after')
    def populate_frame_analysis(self):
        """Safely get frame_analysis from SQLAlchemy property if not set"""
        if hasattr(self, '__pydantic_validator__'):
            # This is during validation, skip
            return self
        return self

# Prediction Schemas
class PredictionResult(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    
    is_deepfake: bool
    confidence_score: float
    analysis_details: dict
    frame_analysis: Optional[FrameAnalysisSummary] = None
    suggestions: List[str] = []

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
