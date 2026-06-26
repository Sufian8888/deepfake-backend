from fastapi import APIRouter, HTTPException, status
from resend.exceptions import ResendError

from app.config import settings
from app.schemas import ContactRequest, MessageResponse
from app.services.email_service import send_contact_email

router = APIRouter()


@router.post("/contact", response_model=MessageResponse)
async def submit_contact_form(payload: ContactRequest):
    """Send contact form submission to the admin email."""
    try:
        await send_contact_email(
            name=payload.name,
            email=payload.email,
            subject=payload.subject,
            message=payload.message,
            admin_email=settings.ADMIN_NOTIFICATION_EMAIL,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc
    except ResendError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc) or "Failed to send your message.",
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to send your message. Please try again later.",
        ) from exc

    return {"message": "Your message has been sent successfully."}
