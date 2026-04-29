from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File
from sqlalchemy.orm import Session
from datetime import datetime
import os

from app.database import get_db
from app.models import User, Video, PredictionStatus
from app.schemas import VideoResponse
from app.auth import get_current_user
from app.utils import save_upload_file, get_file_size, validate_file_type, upload_to_cloudinary
from app.config import settings

router = APIRouter()

@router.post("/video", response_model=VideoResponse, status_code=status.HTTP_201_CREATED)
async def upload_video(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Upload a video file for deepfake detection"""
    
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
    
    # Upload to Cloudinary
    try:
        cloud_url = upload_to_cloudinary(content, file.filename)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to upload video: {str(e)}"
        )
    
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
    
    return new_video

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
    
    return videos

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
    
    return video

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
