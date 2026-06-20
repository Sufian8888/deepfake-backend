from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, case, and_
from sqlalchemy.orm import Session, joinedload, load_only

from app.database import get_db
from app.models import User, Video, UserRole, PredictionStatus
from app.schemas import (
    AdminStats,
    AdminActivityItem,
    AdminVideoListItem,
    AdminUserListItem,
    AdminUserUpdate,
)
from app.auth import get_current_admin
from app.services.audit_log import record_audit_log

router = APIRouter()

@router.get("/stats", response_model=AdminStats)
async def get_admin_stats(
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Get admin dashboard statistics"""
    completed = PredictionStatus.COMPLETED.value
    pending_statuses = [
        PredictionStatus.PENDING.value,
        PredictionStatus.PROCESSING.value,
    ]
    cutoff = datetime.utcnow() - timedelta(hours=24)

    video_stats = db.query(
        func.count(Video.id).label("total_videos"),
        func.count(case((Video.status == completed, 1))).label("total_predictions"),
        func.count(case((Video.is_deepfake == True, 1))).label("deepfake_detected"),
        func.count(
            case((and_(Video.status == completed, Video.is_deepfake == False), 1))
        ).label("genuine_detected"),
        func.count(case((Video.status.in_(pending_statuses), 1))).label("pending_analyses"),
        func.coalesce(func.sum(Video.file_size), 0).label("storage_bytes"),
    ).one()

    total_users = db.query(func.count(User.id)).scalar() or 0
    active_users_24h = db.query(func.count(func.distinct(Video.user_id))).filter(
        Video.uploaded_at >= cutoff
    ).scalar() or 0

    storage_used_mb = round((video_stats.storage_bytes or 0) / (1024 * 1024), 2)

    return {
        "total_users": total_users,
        "total_videos": video_stats.total_videos or 0,
        "total_predictions": video_stats.total_predictions or 0,
        "deepfake_detected": video_stats.deepfake_detected or 0,
        "genuine_detected": video_stats.genuine_detected or 0,
        "pending_analyses": video_stats.pending_analyses or 0,
        "storage_used_mb": storage_used_mb,
        "active_users_24h": active_users_24h,
    }


@router.get("/recent-activity", response_model=list[AdminActivityItem])
async def get_admin_recent_activity(
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
    limit: int = Query(20, ge=1, le=50),
):
    """Get recent activity across all users (admin only)"""
    videos = (
        db.query(Video)
        .options(
            load_only(
                Video.id,
                Video.original_filename,
                Video.status,
                Video.uploaded_at,
                Video.processed_at,
                Video.is_deepfake,
            ),
            joinedload(Video.user).load_only(User.username),
        )
        .order_by(Video.uploaded_at.desc())
        .limit(limit)
        .all()
    )

    activities = []
    for video in videos:
        if video.status == PredictionStatus.COMPLETED.value:
            activity_type = "result"
            timestamp = video.processed_at or video.uploaded_at
        elif video.status in (
            PredictionStatus.PENDING.value,
            PredictionStatus.PROCESSING.value,
        ):
            activity_type = "analysis"
            timestamp = video.uploaded_at
        else:
            activity_type = "upload"
            timestamp = video.uploaded_at

        activities.append({
            "id": video.id,
            "type": activity_type,
            "filename": video.original_filename,
            "user_name": video.user.username if video.user else "Unknown",
            "timestamp": timestamp,
            "status": video.status,
            "is_deepfake": video.is_deepfake,
        })

    return activities

@router.get("/users", response_model=list[AdminUserListItem])
async def get_all_users(
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100)
):
    """Get all users (admin only)"""
    users = db.query(User).order_by(User.created_at.desc()).offset(skip).limit(limit).all()
    return users

@router.get("/users/{user_id}", response_model=AdminUserListItem)
async def get_user_details(
    user_id: int,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Get specific user details"""
    user = db.query(User).filter(User.id == user_id).first()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    
    return user

@router.patch("/users/{user_id}", response_model=AdminUserListItem)
async def update_user(
    user_id: int,
    user_update: AdminUserUpdate,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Update user (activate/deactivate, change role)"""
    user = db.query(User).filter(User.id == user_id).first()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    
    # Update fields
    if user_update.is_active is not None:
        user.is_active = user_update.is_active
    
    if user_update.role is not None:
        user.role = user_update.role
    
    db.commit()
    db.refresh(user)
    
    return user

@router.delete("/users/{user_id}")
async def delete_user(
    user_id: int,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Delete a user (admin only)"""
    user = db.query(User).filter(User.id == user_id).first()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    
    # Prevent admin from deleting themselves
    if user.id == current_admin.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete your own account"
        )
    
    deleted_email = user.email
    db.delete(user)

    record_audit_log(
        user_id=current_admin.id,
        action="admin.delete_user",
        entity_type="user",
        entity_id=user_id,
        details={"deleted_email": deleted_email},
    )
    db.commit()
    
    return {"message": "User deleted successfully"}

@router.get("/videos", response_model=list[AdminVideoListItem])
async def get_all_videos(
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100)
):
    """Get all videos from all users (admin only)"""
    videos = (
        db.query(Video)
        .options(
            load_only(
                Video.id,
                Video.filename,
                Video.original_filename,
                Video.file_size,
                Video.file_type,
                Video.status,
                Video.is_deepfake,
                Video.confidence_score,
                Video.uploaded_at,
            ),
            joinedload(Video.user).load_only(User.email, User.username),
        )
        .order_by(Video.uploaded_at.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )

    return [
        {
            "id": video.id,
            "filename": video.filename,
            "original_filename": video.original_filename,
            "file_size": video.file_size,
            "file_type": video.file_type,
            "status": video.status,
            "is_deepfake": video.is_deepfake,
            "confidence_score": video.confidence_score,
            "uploaded_at": video.uploaded_at,
            "user_email": video.user.email if video.user else None,
            "user_name": video.user.username if video.user else None,
        }
        for video in videos
    ]
