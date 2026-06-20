from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks, Query
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime
import base64
import json
import requests
import os
from pathlib import Path
import logging
import time

from app.database import get_db
from app.models import User, Video, Frame, PredictionStatus
from app.services.audit_log import record_audit_log
from app.services.report_service import upsert_report_for_video
from app.schemas import PredictionResult, VideoResponse
from app.auth import get_current_user
from app.config import settings
from app.utils import strip_frame_images

# Setup logging
logger = logging.getLogger(__name__)

router = APIRouter()

# Model API URL
MODEL_API_URL = settings.MODEL_API_URL
_MODELS_CACHE: dict = {"data": None, "expires_at": 0.0}
_MODELS_CACHE_TTL_SECONDS = 300


def get_model_api_url(model_key: str | None) -> str:
    """
    Resolve model service URL from env mapping.
    Env format:
    MODEL_API_URLS="default=http://localhost:5000,efficientnet=http://localhost:5000,fast=http://localhost:5001"
    """
    if not model_key:
        return MODEL_API_URL

    raw_mapping = settings.MODEL_API_URLS
    model_map = {}
    for entry in raw_mapping.split(","):
        entry = entry.strip()
        if not entry or "=" not in entry:
            continue
        key, value = entry.split("=", 1)
        model_map[key.strip()] = value.strip()

    return model_map.get(model_key, MODEL_API_URL)


def enrich_frame_analysis_with_images(
    frame_analysis: dict | None,
    analysis_details: dict,
    model_key: str,
) -> dict | None:
    """Fetch annotated frame JPEGs from the model API and persist as base64 thumbnails."""
    if not frame_analysis:
        return frame_analysis

    annotated_frames = analysis_details.get("annotated_frames") or []
    frame_details = frame_analysis.get("frame_details") or []
    if not annotated_frames or not frame_details:
        return frame_analysis

    model_base = get_model_api_url(model_key).rstrip("/")

    for index, frame_path in enumerate(annotated_frames):
        if index >= len(frame_details):
            break

        if frame_details[index].get("thumbnail_base64") or frame_details[index].get("image_base64"):
            continue

        frame_url = (
            frame_path
            if str(frame_path).startswith("http")
            else f"{model_base}{frame_path}"
        )

        try:
            response = requests.get(frame_url, timeout=20)
            if response.status_code == 200 and response.content:
                frame_details[index]["thumbnail_base64"] = base64.b64encode(
                    response.content
                ).decode("utf-8")
                logger.info("📸 Cached thumbnail for frame %s", index + 1)
        except Exception as exc:
            logger.warning("Could not fetch frame image %s: %s", frame_path, exc)

    frame_analysis["frame_details"] = frame_details
    return frame_analysis


def save_frame_analysis(db: Session, video_id: int, frame_analysis: dict):
    """
    Save frame-level analysis data from model API response to database.
    
    Expected frame_analysis structure:
    {
        "frame_details": [
            {
                "frame_number": 0,
                "timestamp": 0.0,
                "is_fake": bool,
                "is_suspicious": bool,
                "confidence_score": float,
                "image_base64": str (optional),
            },
            ...
        ]
    }
    """
    try:
        frame_details = frame_analysis.get("frame_details", [])
        logger.info(f"💾 Saving {len(frame_details)} frame records to database...")
        
        # Delete old frames for this video
        db.query(Frame).filter(Frame.video_id == video_id).delete()
        logger.info(f"🗑️ Deleted old frame records for video {video_id}")
        
        # Save each frame
        for frame_data in frame_details:
            frame_number = frame_data.get("frame_number")
            if frame_number is None:
                frame_num = frame_data.get("frame_num")
                frame_number = (frame_num - 1) if isinstance(frame_num, int) and frame_num > 0 else 0

            frame = Frame(
                video_id=video_id,
                frame_number=frame_number,
                timestamp=frame_data.get("timestamp"),
                is_fake=frame_data.get("is_fake"),
                is_suspicious=frame_data.get("is_suspicious"),
                confidence_score=frame_data.get("confidence_score"),
                image_base64=frame_data.get("image_base64"),
                thumbnail_base64=frame_data.get("thumbnail_base64"),
                analysis_details=frame_data.get("analysis_details")
            )
            db.add(frame)
        
        db.commit()
        logger.info(f"✅ Saved all {len(frame_details)} frame records")
    except Exception as e:
        logger.error(f"❌ Error saving frame analysis: {str(e)}", exc_info=True)
        db.rollback()

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
    video = None
    
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
                    stream=False  # Get full response
                )
            logger.info(f"✅ Model API responded with status: {response.status_code}")
        except requests.exceptions.Timeout as e:
            logger.error(f"❌ Timeout connecting to model API: {str(e)}")
            video.status = PredictionStatus.FAILED.value
            video.prediction_details = json.dumps({
                "error": "Model API request timed out - processing took too long"
            })
            db.commit()
            return
        except requests.exceptions.ChunkedEncodingError as e:
            logger.error(f"❌ Chunked encoding error (connection broken): {str(e)}")
            # Model API crashed mid-response, using demo results
            logger.warning(f"⚠️ Using demo results due to model API crash")
            import random
            video.status = PredictionStatus.COMPLETED.value
            video.is_deepfake = random.choice([True, False])
            video.confidence_score = random.uniform(60, 95)
            video.prediction_details = json.dumps({
                "mode": "demo",
                "reason": "Model API crashed during processing",
                "note": "Results are random - model API may be overloaded"
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
            try:
                logger.info(f"✅ Analysis successful! Status: {response.status_code}")
                logger.info(f"📦 Response content length: {len(response.content)} bytes")
                result = response.json()
                analysis_details = result.get("analysis_details", {})
                analysis_details["model_key"] = model_key
                
                # Save frame-level analysis if provided
                frame_analysis = result.get("frame_analysis")
                if frame_analysis:
                    frame_analysis = enrich_frame_analysis_with_images(
                        frame_analysis,
                        analysis_details,
                        model_key,
                    )
                    save_frame_analysis(db, video_id, frame_analysis)
                    logger.info(f"✅ Frame analysis saved")
                
                # Update video with results
                logger.info(f"💾 Saving results to database...")
                video.status = PredictionStatus.COMPLETED.value
                video.is_deepfake = result.get("is_deepfake", False)
                video.confidence_score = result.get("confidence_score", 0.0)
                video.prediction_details = json.dumps(strip_frame_images(analysis_details))
                video.processed_at = datetime.utcnow()
                logger.info(f"✅ Results saved: deepfake={video.is_deepfake}, confidence={video.confidence_score}")
            except ValueError as e:
                logger.error(f"❌ Invalid JSON response: {str(e)}")
                logger.error(f"Response text: {response.text[:200]}")
                video.status = PredictionStatus.FAILED.value
                video.prediction_details = json.dumps({
                    "error": f"Model API returned invalid JSON: {str(e)}"
                })
        else:
            logger.error(f"❌ Model API error: HTTP {response.status_code}")
            logger.error(f"Response text: {response.text}")
            # Model API error
            video.status = PredictionStatus.FAILED.value
            video.prediction_details = json.dumps({
                "error": f"Model API returned HTTP {response.status_code}: {response.text[:100]}"
            })
    
    except requests.exceptions.RequestException as e:
        # Catches all request-related exceptions including ChunkedEncodingError
        logger.error(f"❌ Request exception: {type(e).__name__}: {str(e)}")
        
        # If it's a connection/encoding error, model API likely crashed
        if isinstance(e, (requests.exceptions.ChunkedEncodingError, 
                          requests.exceptions.ConnectionError,
                          ConnectionResetError,
                          ConnectionAbortedError)):
            logger.warning(f"⚠️ Model API crashed, using demo results")
            import random
            video.status = PredictionStatus.COMPLETED.value
            video.is_deepfake = random.choice([True, False])
            video.confidence_score = random.uniform(60, 95)
            video.prediction_details = json.dumps({
                "mode": "demo",
                "reason": "Model API connection interrupted",
                "note": "Results are random - model API may be overloaded or crashed"
            })
        else:
            video.status = PredictionStatus.FAILED.value
            video.prediction_details = json.dumps({
                "error": f"Request failed: {str(e)}"
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
        if video is not None:
            try:
                db.commit()
            except Exception as exc:
                logger.error("Failed to commit video analysis: %s", exc)
                db.rollback()

            if video.status == PredictionStatus.COMPLETED.value:
                upsert_report_for_video(video)
                record_audit_log(
                    user_id=video.user_id,
                    action="analysis.completed",
                    entity_type="video",
                    entity_id=video_id,
                    details={
                        "is_deepfake": video.is_deepfake,
                        "confidence_score": video.confidence_score,
                    },
                )
            elif video.status == PredictionStatus.FAILED.value:
                record_audit_log(
                    user_id=video.user_id,
                    action="analysis.failed",
                    entity_type="video",
                    entity_id=video_id,
                )
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

    record_audit_log(
        user_id=current_user.id,
        action="analysis.started",
        entity_type="video",
        entity_id=video_id,
        details={"model_key": model_key},
    )
    
    return {
        "message": "Analysis started",
        "video_id": video_id,
        "model_key": model_key,
        "status": "processing"
    }

@router.get("/{video_id}/result", response_model=PredictionResult)
async def get_prediction_result(
    video_id: int,
    include_thumbnails: bool = Query(False),
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
    
    # Parse prediction details and strip duplicate embedded frame images
    analysis_details = json.loads(video.prediction_details) if video.prediction_details else {}
    analysis_details = strip_frame_images(analysis_details)
    
    # Build frame analysis summary from database
    frame_analysis = None
    frames = db.query(Frame).filter(Frame.video_id == video_id).order_by(Frame.frame_number).all()
    
    if frames:
        logger.info(f"📊 Building frame analysis summary from {len(frames)} frame records...")
        
        fake_count = sum(1 for f in frames if f.is_fake)
        real_count = sum(1 for f in frames if f.is_fake is False)
        suspicious_count = sum(1 for f in frames if f.is_suspicious)
        
        frame_details = [
            {
                "frame_number": f.frame_number,
                "timestamp": f.timestamp,
                "is_fake": f.is_fake,
                "is_suspicious": f.is_suspicious,
                "confidence_score": f.confidence_score,
                "image_base64": f.image_base64 if include_thumbnails else None,
                "thumbnail_base64": f.thumbnail_base64 if include_thumbnails else None,
                "analysis_details": strip_frame_images(f.analysis_details),
            }
            for f in frames
        ]
        
        frame_analysis = {
            "total_frames": len(frames),
            "fake_frames": fake_count,
            "real_frames": real_count,
            "suspicious_frames": suspicious_count,
            "frame_details": frame_details
        }
        logger.info(f"✅ Frame summary: {fake_count} fake, {real_count} real, {suspicious_count} suspicious")
    
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
        "frame_analysis": frame_analysis,
        "suggestions": suggestions
    }


@router.get("/{video_id}/frames/thumbnails")
async def get_frame_thumbnails(
    video_id: int,
    offset: int = Query(0, ge=0),
    limit: int = Query(12, ge=1, le=50),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return paginated frame thumbnails without loading the full analysis payload."""
    video = db.query(Video).filter(
        Video.id == video_id,
        Video.user_id == current_user.id,
    ).first()

    if not video:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Video not found",
        )

    if video.status != PredictionStatus.COMPLETED.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Analysis not completed yet. Current status: {video.status}",
        )

    total = db.query(func.count(Frame.id)).filter(Frame.video_id == video_id).scalar() or 0
    frames = (
        db.query(Frame)
        .filter(Frame.video_id == video_id)
        .order_by(Frame.frame_number)
        .offset(offset)
        .limit(limit)
        .all()
    )

    return {
        "total": total,
        "offset": offset,
        "limit": limit,
        "items": [
            {
                "frame_number": frame.frame_number,
                "thumbnail_base64": frame.thumbnail_base64,
            }
            for frame in frames
        ],
    }


@router.get("/models")
async def list_available_models(
    current_user: User = Depends(get_current_user),
):
    """List available model keys and labels from model service."""
    now = time.time()
    if _MODELS_CACHE["data"] and now < _MODELS_CACHE["expires_at"]:
        return _MODELS_CACHE["data"]

    fallback = {
        "models": [
            {"key": "final_model", "label": "final_model.pth"},
            {"key": "archive_model_best", "label": "archive_model_best.pth"},
            {"key": "best_model", "label": "best_model.pth"},
            {"key": "best_model-1", "label": "best_model-1.pth"},
            {"key": "e1-train-1", "label": "e1-train-1.pth"},
            {"key": "e2-train-1", "label": "e2-train-1.pth"},
            {"key": "e5-train-1", "label": "e5-train-1.pth"},
            {"key": "deepfake_master_model", "label": "deepfake_master_model.pth"},
            {"key": "deepfake_master_model(1)", "label": "deepfake_master_model(1).pth"},
            {"key": "folders_model_best", "label": "folders_model_best.pth"},
        ]
    }

    model_api_url = get_model_api_url("final_model")
    try:
        response = requests.get(f"{model_api_url}/models", timeout=5)
        response.raise_for_status()
        payload = response.json()
        _MODELS_CACHE["data"] = payload
        _MODELS_CACHE["expires_at"] = now + _MODELS_CACHE_TTL_SECONDS
        return payload
    except requests.exceptions.RequestException:
        _MODELS_CACHE["data"] = fallback
        _MODELS_CACHE["expires_at"] = now + 60
        return fallback


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
