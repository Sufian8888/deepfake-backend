import logging
from typing import Any, Optional

from app.database import SessionLocal
from app.models import AuditLog

logger = logging.getLogger(__name__)


def record_audit_log(
    *,
    action: str,
    user_id: Optional[int] = None,
    entity_type: Optional[str] = None,
    entity_id: Optional[int] = None,
    details: Optional[dict[str, Any]] = None,
) -> None:
    """Append an audit log in its own DB session so main requests never fail."""
    log_db = SessionLocal()
    try:
        log_db.add(
            AuditLog(
                user_id=user_id,
                action=action,
                entity_type=entity_type,
                entity_id=entity_id,
                details=details,
            )
        )
        log_db.commit()
    except Exception as exc:
        log_db.rollback()
        logger.warning("Failed to record audit log (%s): %s", action, exc)
    finally:
        log_db.close()
