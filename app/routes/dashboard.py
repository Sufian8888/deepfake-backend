from fastapi import APIRouter, Depends
from sqlalchemy import func, case
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User, Video, PredictionStatus
from app.schemas import DashboardStats, VideoResponse
from app.auth import get_current_user

router = APIRouter()

@router.get("/stats", response_model=DashboardStats)
async def get_dashboard_stats(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get user dashboard statistics"""

    counts = db.query(
        func.count(Video.id).label("total_videos"),
        func.sum(case((Video.status == PredictionStatus.COMPLETED.value, 1), else_=0)).label("total_predictions"),
        func.sum(case((Video.is_deepfake == True, 1), else_=0)).label("deepfakes_found"),
        func.sum(
            case(
                (Video.status.in_([PredictionStatus.PENDING.value, PredictionStatus.PROCESSING.value]), 1),
                else_=0,
            )
        ).label("pending_analyses"),
    ).filter(Video.user_id == current_user.id).one()

    total_videos = int(counts.total_videos or 0)
    total_predictions = int(counts.total_predictions or 0)
    deepfakes_found = int(counts.deepfakes_found or 0)
    pending_analyses = int(counts.pending_analyses or 0)
    genuine_videos = total_predictions - deepfakes_found
    success_rate = (total_predictions / total_videos * 100) if total_videos > 0 else 0.0

    return {
        "total_videos": total_videos,
        "total_predictions": total_predictions,
        "deepfakes_found": deepfakes_found,
        "genuine_videos": genuine_videos,
        "pending_analyses": pending_analyses,
        "success_rate": success_rate
    }

@router.get("/recent-activity", response_model=list[VideoResponse])
async def get_recent_activity(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    limit: int = 10
):
    """Get user's recent video activity"""
    recent_videos = db.query(Video).filter(
        Video.user_id == current_user.id
    ).order_by(Video.uploaded_at.desc()).limit(limit).all()
    
    return recent_videos
