import logging
from datetime import datetime

from app.database import SessionLocal
from app.models import Report, PredictionStatus, Video

logger = logging.getLogger(__name__)


def upsert_report_for_video(video: Video) -> None:
    """Create or refresh a report row when analysis completes (isolated session)."""
    if video.status != PredictionStatus.COMPLETED.value or video.is_deepfake is None:
        return

    report_db = SessionLocal()
    try:
        verdict = "FAKE" if video.is_deepfake else "REAL"
        report = report_db.query(Report).filter(Report.video_id == video.id).first()

        if report:
            report.verdict = verdict
            report.is_deepfake = video.is_deepfake
            report.confidence_score = video.confidence_score
            report.updated_at = datetime.utcnow()
        else:
            report_db.add(
                Report(
                    user_id=video.user_id,
                    video_id=video.id,
                    verdict=verdict,
                    is_deepfake=video.is_deepfake,
                    confidence_score=video.confidence_score,
                )
            )

        report_db.commit()
    except Exception as exc:
        report_db.rollback()
        logger.warning("Failed to upsert report for video %s: %s", video.id, exc)
    finally:
        report_db.close()
