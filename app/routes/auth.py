import random

from resend.exceptions import ResendError
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from datetime import timedelta

from app.database import get_db
from app.models import User, UserRole
from app.schemas import (
    UserCreate,
    UserLogin,
    UserResponse,
    Token,
    SendOTPRequest,
    VerifyOTPRequest,
    MessageResponse,
)
from app.services.email_service import send_otp_email
from app.services.otp_store import otp_store
from app.auth import (
    verify_password,
    get_password_hash,
    create_access_token,
    get_current_user
)
from app.config import settings

router = APIRouter()


def build_user_response(user: User) -> UserResponse:
    return UserResponse(
        id=user.id,
        email=user.email,
        username=user.username,
        role=user.role,
        is_active=user.is_active,
        subscription_plan=user.subscription_plan,
        subscription_status=user.subscription_status,
        subscription_cycle=user.subscription_cycle,
        stripe_customer_id=user.stripe_customer_id,
        stripe_subscription_id=user.stripe_subscription_id,
        stripe_price_id=user.stripe_price_id,
        subscription_current_period_end=user.subscription_current_period_end,
        subscription_updated_at=user.subscription_updated_at,
        created_at=user.created_at,
    )

@router.post("/signup", response_model=Token, status_code=status.HTTP_201_CREATED)
async def signup(user_data: UserCreate, db: Session = Depends(get_db)):
    """Register a new user"""
    # Check if user already exists
    existing_user = db.query(User).filter(User.email == user_data.email).first()
    
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered"
        )
    
    # Generate username from email (before @)
    username = user_data.email.split('@')[0]
    
    # Check if username is taken, make it unique if needed
    base_username = username
    counter = 1
    while db.query(User).filter(User.username == username).first():
        username = f"{base_username}{counter}"
        counter += 1
    
    # Create new user
    hashed_password = get_password_hash(user_data.password)
    new_user = User(
        email=user_data.email,
        username=username,
        hashed_password=hashed_password,
        role=UserRole.USER.value
    )
    
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    
    # Create access token
    access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": new_user.email}, expires_delta=access_token_expires
    )
    
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user": build_user_response(new_user)
    }

@router.post("/login", response_model=Token)
async def login(credentials: UserLogin, db: Session = Depends(get_db)):
    """Login user and return JWT token"""
    # Find user by email or username
    user = db.query(User).filter(
        (User.email == credentials.username) | (User.username == credentials.username)
    ).first()
    
    if not user or not verify_password(credentials.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Inactive user account"
        )
    
    # Create access token
    access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user.email}, expires_delta=access_token_expires
    )
    
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user": build_user_response(user)
    }

@router.get("/me", response_model=UserResponse)
async def get_me(current_user: User = Depends(get_current_user)):
    """Get current user information"""
    return UserResponse(
        id=current_user.id,
        email=current_user.email,
        username=current_user.username,
        role=current_user.role,
        is_active=current_user.is_active,
        subscription_plan=current_user.subscription_plan,
        subscription_status=current_user.subscription_status,
        subscription_cycle=current_user.subscription_cycle,
        stripe_customer_id=current_user.stripe_customer_id,
        stripe_subscription_id=current_user.stripe_subscription_id,
        stripe_price_id=current_user.stripe_price_id,
        subscription_current_period_end=current_user.subscription_current_period_end,
        subscription_updated_at=current_user.subscription_updated_at,
        created_at=current_user.created_at,
    )

@router.post("/logout")
async def logout(current_user: User = Depends(get_current_user)):
    """Logout user (client should delete token)"""
    return {"message": "Successfully logged out"}


@router.post("/send-otp", response_model=MessageResponse)
async def send_otp(payload: SendOTPRequest):
    """Generate a 6-digit OTP, store it in memory, and email it to the user."""
    email = payload.email.lower()
    otp = f"{random.randint(0, 999999):06d}"
    otp_store[email] = otp

    try:
        await send_otp_email(email, otp)
    except ValueError as exc:
        otp_store.pop(email, None)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc
    except ResendError as exc:
        otp_store.pop(email, None)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc) or "Failed to send verification email.",
        ) from exc
    except Exception as exc:
        otp_store.pop(email, None)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to send verification email. Please try again later.",
        ) from exc

    return {"message": "Verification code sent to your email."}


@router.post("/verify-otp", response_model=MessageResponse)
async def verify_otp(payload: VerifyOTPRequest):
    """Verify the OTP for an email address and remove it after success."""
    email = payload.email.lower()
    stored_otp = otp_store.get(email)

    if not stored_otp:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No verification code found for this email. Please request a new one.",
        )

    if stored_otp != payload.otp:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid verification code.",
        )

    del otp_store[email]
    return {"message": "Email verified successfully."}
