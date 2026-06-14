from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime
import os

from app.database import get_db
from app.models import User, Video, PredictionStatus
from app.schemas import VideoResponse
from app.auth import get_current_user
from app.utils import save_upload_file, get_file_size, validate_file_type, upload_to_cloudinary
from app.config import settings

router = APIRouter()

FREE_MONTHLY_UPLOAD_LIMIT = 5


def is_premium_user(current_user: User) -> bool:
    return (
        current_user.subscription_plan in {"pro", "enterprise"}
        and current_user.subscription_status == "active"
    )

@router.post("/video", response_model=VideoResponse, status_code=status.HTTP_201_CREATED)
async def upload_video(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Upload a video file for deepfake detection"""

    if not is_premium_user(current_user):
        month_start = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        uploads_this_month = db.query(func.count(Video.id)).filter(
            Video.user_id == current_user.id,
            Video.uploaded_at >= month_start
        ).scalar() or 0

        if uploads_this_month >= FREE_MONTHLY_UPLOAD_LIMIT:
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail="Free plan limit reached. Upgrade to Pro for unlimited uploads.",
            )
    
    # Validate file type
    try:
        file_type = validate_file_type(file.filename)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    
    # Check file size (read first to get size)
    content = await file.read()
    file_size = len(content)
    
    if file_size > settings.MAX_FILE_SIZE:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File too large. Max size: {settings.MAX_FILE_SIZE / 1024 / 1024}MB"
        )
    
    # Upload to Cloudinary (optional - if credentials not set, returns None)
    cloud_url = upload_to_cloudinary(content, file.filename)
    
    # Reset file pointer and save local copy as backup
    await file.seek(0)
    file_path = save_upload_file(file, file.filename)
    
    # Create database record
    new_video = Video(
        user_id=current_user.id,
        filename=os.path.basename(file_path),
        original_filename=file.filename,
        file_path=file_path,
        cloud_url=cloud_url,
        file_size=file_size,
        file_type=file_type,
        status=PredictionStatus.PENDING.value
    )
    
    db.add(new_video)
    db.commit()
    db.refresh(new_video)
    
    # Convert ORM object to schema, handling frame_analysis property
    video_dict = {
        'id': new_video.id,
        'filename': new_video.filename,
        'original_filename': new_video.original_filename,
        'file_size': new_video.file_size,
        'file_type': new_video.file_type,
        'status': new_video.status,
        'is_deepfake': new_video.is_deepfake,
        'confidence_score': new_video.confidence_score,
        'prediction_details': new_video.prediction_details,
        'cloud_url': new_video.cloud_url,
        'uploaded_at': new_video.uploaded_at,
        'processed_at': new_video.processed_at,
        'frame_analysis': new_video.frame_analysis  # Explicitly get property
    }
    
    return VideoResponse(**video_dict)

@router.get("/videos", response_model=list[VideoResponse])
async def get_user_videos(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    skip: int = 0,
    limit: int = 10
):
    """Get all videos uploaded by current user"""
    videos = db.query(Video).filter(
        Video.user_id == current_user.id
    ).order_by(Video.uploaded_at.desc()).offset(skip).limit(limit).all()
    
    # Convert ORM objects to schemas, handling frame_analysis property
    result = []
    for video in videos:
        video_dict = {
            'id': video.id,
            'filename': video.filename,
            'original_filename': video.original_filename,
            'file_size': video.file_size,
            'file_type': video.file_type,
            'status': video.status,
            'is_deepfake': video.is_deepfake,
            'confidence_score': video.confidence_score,
            'prediction_details': video.prediction_details,
            'cloud_url': video.cloud_url,
            'uploaded_at': video.uploaded_at,
            'processed_at': video.processed_at,
            'frame_analysis': video.frame_analysis  # Explicitly get property
        }
        result.append(VideoResponse(**video_dict))
    
    return result

@router.get("/videos/{video_id}", response_model=VideoResponse)
async def get_video(
    video_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get specific video by ID"""
    video = db.query(Video).filter(
        Video.id == video_id,
        Video.user_id == current_user.id
    ).first()
    
    if not video:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Video not found"
        )
    
    # Convert ORM object to schema, handling frame_analysis property
    video_dict = {
        'id': video.id,
        'filename': video.filename,
        'original_filename': video.original_filename,
        'file_size': video.file_size,
        'file_type': video.file_type,
        'status': video.status,
        'is_deepfake': video.is_deepfake,
        'confidence_score': video.confidence_score,
        'prediction_details': video.prediction_details,
        'cloud_url': video.cloud_url,
        'uploaded_at': video.uploaded_at,
        'processed_at': video.processed_at,
        'frame_analysis': video.frame_analysis  # Explicitly get property
    }
    
    return VideoResponse(**video_dict)

@router.delete("/videos/{video_id}")
async def delete_video(
    video_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Delete a video"""
    video = db.query(Video).filter(
        Video.id == video_id,
        Video.user_id == current_user.id
    ).first()
    
    if not video:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Video not found"
        )
    
    # Delete from database
    db.delete(video)
    db.commit()
    
    return {"message": "Video deleted successfully"}
