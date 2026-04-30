from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks
from sqlalchemy.orm import Session
from datetime import datetime
import json
import requests
import os
from pathlib import Path
import logging

from app.database import get_db
from app.models import User, Video, PredictionStatus
from app.schemas import PredictionResult, VideoResponse
from app.auth import get_current_user

# Setup logging
logger = logging.getLogger(__name__)

router = APIRouter()

# Model API URL
MODEL_API_URL = os.getenv("MODEL_API_URL", "http://localhost:5000")


def get_model_api_url(model_key: str | None) -> str:
    """
    Resolve model service URL from env mapping.
    Env format:
    MODEL_API_URLS="default=http://localhost:5000,efficientnet=http://localhost:5000,fast=http://localhost:5001"
    """
    if not model_key:
        return MODEL_API_URL

    raw_mapping = os.getenv("MODEL_API_URLS", "")
    model_map = {}
    for entry in raw_mapping.split(","):
        entry = entry.strip()
        if not entry or "=" not in entry:
            continue
        key, value = entry.split("=", 1)
        model_map[key.strip()] = value.strip()
    
    return model_map.get(model_key, MODEL_API_URL)

def deepfake_analysis(video_id: int, model_key: str = "default"):
    """
    Call the model API to analyze video for deepfake detection
    Background task that creates its own database session
    """
    import tempfile
    from app.database import SessionLocal
    
    logger.info(f"🎬 BACKGROUND TASK STARTED: video_id={video_id}, model_key={model_key}")
    
    db = SessionLocal()
    temp_video_path = None
    
    try:
        # Fetch video from database
        logger.info(f"📁 Fetching video {video_id} from database...")
        video = db.query(Video).filter(Video.id == video_id).first()
        if not video:
            logger.error(f"❌ Video {video_id} not found!")
            return
        
        logger.info(f"✅ Video found: {video.original_filename}")
        
        # Update status to processing
        logger.info(f"⏳ Setting status to PROCESSING...")
        video.status = PredictionStatus.PROCESSING.value
        db.commit()
        
        # Download from Cloudinary if available, otherwise use local file
        temp_video_path = None
        if video.cloud_url:
            logger.info(f"☁️ Downloading from Cloudinary: {video.cloud_url[:50]}...")
            # Download from Cloudinary
            try:
                response = requests.get(video.cloud_url, timeout=60)
                response.raise_for_status()
                
                with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as tmp_file:
                    temp_video_path = tmp_file.name
                    tmp_file.write(response.content)
                logger.info(f"✅ Downloaded to temp: {temp_video_path}")
            except Exception as e:
                logger.error(f"❌ Cloudinary download failed: {str(e)}")
                video.status = PredictionStatus.FAILED.value
                video.prediction_details = json.dumps({
                    "error": f"Failed to download video from Cloudinary: {str(e)}"
                })
                db.commit()
                return
        else:
            logger.info(f"📂 Using local file: {video.file_path}")
            # Fall back to local file if cloud_url is not available
            backend_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            absolute_video_path = os.path.join(backend_dir, video.file_path)
            
            logger.info(f"📍 Full path: {absolute_video_path}")
            if not os.path.exists(absolute_video_path):
                logger.error(f"❌ Local file not found: {absolute_video_path}")
                video.status = PredictionStatus.FAILED.value
                video.prediction_details = json.dumps({
                    "error": "Uploaded video file not found on backend server"
                })
                db.commit()
                return
            
            logger.info(f"✅ Local file found, copying to temp...")
            # Copy to temp location for processing
            with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as tmp_file:
                temp_video_path = tmp_file.name
                with open(absolute_video_path, "rb") as src:
                    tmp_file.write(src.read())
            logger.info(f"✅ Copied to temp: {temp_video_path}")
        
        # Send the video file to model service
        logger.info(f"🔗 Getting model API URL for: {model_key}")
        model_api_url = get_model_api_url(model_key)
        logger.info(f"🌐 Model API URL: {model_api_url}")

        try:
            logger.info(f"📤 Sending video to model API: {model_api_url}/analyze")
            with open(temp_video_path, "rb") as media_file:
                response = requests.post(
                    f"{model_api_url}/analyze",
                    data={"model_key": model_key},
                    files={
                        "file": (
                            f"video_{video_id}.mp4",
                            media_file,
                            "application/octet-stream",
                        )
                    },
                    timeout=600,  # 10 minutes timeout for large files
                )
            logger.info(f"✅ Model API responded with status: {response.status_code}")
        except requests.exceptions.Timeout as e:
            logger.error(f"❌ Timeout connecting to model API: {str(e)}")
            video.status = PredictionStatus.FAILED.value
            video.prediction_details = json.dumps({
                "error": "Model API request timed out (processing took too long)"
            })
            db.commit()
            return
        except requests.exceptions.ConnectionError as e:
            logger.error(f"❌ Connection error to model API: {str(e)}")
            video.status = PredictionStatus.FAILED.value
            video.prediction_details = json.dumps({
                "error": f"Cannot connect to model API: {str(e)}"
            })
            db.commit()
            return
        
        if response.status_code == 200:
            logger.info(f"✅ Analysis successful!")
            result = response.json()
            analysis_details = result.get("analysis_details", {})
            analysis_details["model_key"] = model_key
            
            # Update video with results
            logger.info(f"💾 Saving results to database...")
            video.status = PredictionStatus.COMPLETED.value
            video.is_deepfake = result.get("is_deepfake", False)
            video.confidence_score = result.get("confidence_score", 0.0)
            video.prediction_details = json.dumps(analysis_details)
            video.processed_at = datetime.utcnow()
            logger.info(f"✅ Results saved: deepfake={video.is_deepfake}, confidence={video.confidence_score}")
        else:
            logger.error(f"❌ Model API error: {response.status_code}")
            # Model API error
            video.status = PredictionStatus.FAILED.value
            video.prediction_details = json.dumps({
                "error": f"Model API error: {response.text}"
            })
    
    except requests.exceptions.RequestException as e:
        # Connection error
        logger.error(f"❌ Request exception: {str(e)}")
        video.status = PredictionStatus.FAILED.value
        video.prediction_details = json.dumps({
            "error": f"Failed to connect to model API: {str(e)}"
        })
    
    except Exception as e:
        # Other errors
        logger.error(f"❌ Unexpected error: {str(e)}", exc_info=True)
        video.status = PredictionStatus.FAILED.value
        video.prediction_details = json.dumps({
            "error": f"Analysis failed: {str(e)}"
        })
    
    finally:
        # Clean up temp file
        logger.info(f"🧹 Cleaning up temp file...")
        if temp_video_path and os.path.exists(temp_video_path):
            try:
                os.remove(temp_video_path)
                logger.info(f"✅ Temp file deleted: {temp_video_path}")
            except Exception as e:
                logger.error(f"❌ Error deleting temp file: {e}")
        
        logger.info(f"💾 Committing database changes...")
        db.commit()
        db.close()
        logger.info(f"✅ Background task completed for video {video_id}")

@router.post("/{video_id}/analyze", response_model=dict)
async def start_analysis(
    video_id: int,
    background_tasks: BackgroundTasks,
    model_key: str = "default",
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Start deepfake analysis for a video"""
    video = db.query(Video).filter(
        Video.id == video_id,
        Video.user_id == current_user.id
    ).first()
    
    if not video:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Video not found"
        )
    
    if video.status != PredictionStatus.PENDING.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Video is already {video.status}"
        )
    
    # Start analysis in background
    background_tasks.add_task(deepfake_analysis, video_id, model_key)
    
    return {
        "message": "Analysis started",
        "video_id": video_id,
        "model_key": model_key,
        "status": "processing"
    }

@router.get("/{video_id}/result", response_model=PredictionResult)
async def get_prediction_result(
    video_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get prediction result for a video"""
    video = db.query(Video).filter(
        Video.id == video_id,
        Video.user_id == current_user.id
    ).first()
    
    if not video:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Video not found"
        )
    
    if video.status != PredictionStatus.COMPLETED.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Analysis not completed yet. Current status: {video.status}"
        )
    
    # Parse prediction details
    analysis_details = json.loads(video.prediction_details) if video.prediction_details else {}
    
    # Generate AI suggestions
    suggestions = []
    if video.is_deepfake:
        suggestions = [
            "⚠️ This video shows signs of manipulation",
            "🔍 Check the source and verify authenticity",
            "📊 Review the detailed analysis for specific anomalies",
            "🚨 Consider reporting if this is being used maliciously"
        ]
    else:
        suggestions = [
            "✅ No significant deepfake indicators detected",
            "💡 Always verify content from multiple sources",
            "📈 The video passed all authenticity checks",
            "✔️ High confidence in the authenticity of this media"
        ]
    
    return {
        "is_deepfake": video.is_deepfake,
        "confidence_score": video.confidence_score,
        "analysis_details": analysis_details,
        "suggestions": suggestions
    }

@router.get("/models")
async def list_available_models(
    current_user: User = Depends(get_current_user),
):
    """List available model keys and labels from model service."""
    model_api_url = get_model_api_url("final_model")
    try:
        response = requests.get(f"{model_api_url}/models", timeout=30)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException:
        return {
            "models": [
                {"key": "final_model", "label": "final_model.pth"},
                {"key": "archive_model_best", "label": "archive_model_best.pth"},
                {"key": "best_model", "label": "best_model.pth"},
                {"key": "best_model-1", "label": "best_model-1.pth"},
                {"key": "e1-train-1", "label": "e1-train-1.pth"},
                {"key": "e2-train-1", "label": "e2-train-1.pth"},
                {"key": "e5-train-1", "label": "e5-train-1.pth"},
                {"key": "folders_model_best", "label": "folders_model_best.pth"},
            ]
        }


@router.get("/{video_id}/status")
async def get_analysis_status(
    video_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Check the status of video analysis"""
    video = db.query(Video).filter(
        Video.id == video_id,
        Video.user_id == current_user.id
    ).first()
    
    if not video:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Video not found"
        )
    
    return {
        "video_id": video.id,
        "status": video.status,
        "uploaded_at": video.uploaded_at,
        "processed_at": video.processed_at
    }
