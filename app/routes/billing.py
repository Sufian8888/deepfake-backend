from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import stripe
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.config import settings
from app.database import get_db
from app.models import User
from app.schemas import (
    BillingCheckoutRequest,
    BillingCheckoutResponse,
    BillingConfirmRequest,
    BillingInfoResponse,
    BillingPortalResponse,
)

router = APIRouter()

stripe.api_key = settings.STRIPE_SECRET_KEY or None

PLAN_PRICE_LOOKUP = {
    "pro": {
        "monthly": settings.STRIPE_PRICE_PRO_MONTHLY,
        "yearly": settings.STRIPE_PRICE_PRO_YEARLY,
    },
    "enterprise": {
        "monthly": settings.STRIPE_PRICE_ENTERPRISE_MONTHLY,
        "yearly": settings.STRIPE_PRICE_ENTERPRISE_YEARLY,
    },
}


def _is_price_id(value: str | None) -> bool:
    return bool(value and value.startswith("price_"))


def _is_product_id(value: str | None) -> bool:
    return bool(value and value.startswith("prod_"))


def _billing_cycle_to_interval(billing_cycle: str) -> str:
    return "month" if billing_cycle == "monthly" else "year"


def _enum_text(value: Any) -> str:
    if hasattr(value, "value"):
        return str(value.value)
    return str(value)


def _to_plain_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}

    if isinstance(value, dict):
        return value

    if hasattr(value, "to_dict_recursive"):
        return value.to_dict_recursive()

    if hasattr(value, "to_dict"):
        return value.to_dict()

    try:
        return dict(value)
    except Exception:
        return {}


def _find_user(db: Session, metadata: dict[str, Any] | None, email: str | None = None) -> User | None:
    user_id = None
    if metadata:
        user_id = metadata.get("user_id")

    if user_id:
        user = db.query(User).filter(User.id == int(user_id)).first()
        if user:
            return user

    if metadata and metadata.get("user_email"):
        email = metadata.get("user_email")

    if email:
        return db.query(User).filter(User.email == email).first()

    return None


def _get_customer_id(user: User) -> str | None:
    if user.stripe_customer_id:
        return user.stripe_customer_id

    if user.stripe_subscription_id:
        try:
            subscription = stripe.Subscription.retrieve(user.stripe_subscription_id)
            customer_id = getattr(subscription, "customer", None)
            if customer_id:
                return str(customer_id)
        except Exception:
            return None

    return None


def _resolve_price_id(configured_id: str, billing_cycle: str) -> str:
    if _is_price_id(configured_id):
        return configured_id

    if not _is_product_id(configured_id):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f'Invalid Stripe identifier "{configured_id}". Use a price_ or prod_ value.',
        )

    if not settings.STRIPE_SECRET_KEY:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Stripe is not configured on the backend.",
        )

    prices = stripe.Price.list(product=configured_id, active=True, limit=100)
    expected_interval = _billing_cycle_to_interval(billing_cycle)

    for price in prices.data:
        recurring = getattr(price, "recurring", None)
        if recurring and getattr(recurring, "interval", None) == expected_interval:
            return price.id

    for price in prices.data:
        if getattr(price, "recurring", None):
            return price.id

    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail=(
            f'No active recurring Stripe price found for product "{configured_id}" '
            f'and cycle "{billing_cycle}".'
        ),
    )


def _apply_subscription_state(
    db: Session,
    user: User,
    *,
    plan: str,
    billing_cycle: str,
    subscription_id: str | None,
    customer_id: str | None,
    price_id: str | None,
    status_value: str,
    current_period_end: int | None,
) -> None:
    user.subscription_plan = plan
    user.subscription_cycle = billing_cycle
    user.subscription_status = status_value
    user.stripe_subscription_id = subscription_id
    user.stripe_customer_id = customer_id or user.stripe_customer_id
    user.stripe_price_id = price_id
    user.subscription_current_period_end = (
        datetime.fromtimestamp(current_period_end, tz=timezone.utc).replace(tzinfo=None)
        if current_period_end
        else None
    )
    user.subscription_updated_at = datetime.utcnow()
    db.commit()


@router.post("/checkout-session", response_model=BillingCheckoutResponse)
async def create_checkout_session(
    payload: BillingCheckoutRequest,
    current_user: User = Depends(get_current_user),
):
    if not settings.STRIPE_SECRET_KEY:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Stripe is not configured. Add STRIPE_SECRET_KEY to the backend .env file.",
        )

    plan = _enum_text(payload.plan)
    billing_cycle = _enum_text(payload.billing_cycle)

    if plan == "free":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Free plan does not require checkout.")

    price_id = PLAN_PRICE_LOOKUP[plan][billing_cycle]
    if not price_id:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Missing Stripe price ID for {plan} ({billing_cycle}).",
        )

    resolved_price_id = _resolve_price_id(price_id, billing_cycle)

    session = stripe.checkout.Session.create(
        mode="subscription",
        customer_email=current_user.email,
        client_reference_id=str(current_user.id),
        line_items=[{"price": resolved_price_id, "quantity": 1}],
        success_url=f"{settings.FRONTEND_URL}/user-dashboard?success=1&plan={plan}&session_id={{CHECKOUT_SESSION_ID}}",
        cancel_url=f"{settings.FRONTEND_URL}/user-dashboard?canceled=1",
        allow_promotion_codes=True,
        metadata={
            "user_id": str(current_user.id),
            "user_email": current_user.email,
            "plan": plan,
            "billing_cycle": billing_cycle,
            "price_id": resolved_price_id,
        },
        subscription_data={
            "metadata": {
                "user_id": str(current_user.id),
                "user_email": current_user.email,
                "plan": plan,
                "billing_cycle": billing_cycle,
                "price_id": resolved_price_id,
            }
        },
    )

    if not session.url:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Stripe did not return a checkout URL.",
        )

    return BillingCheckoutResponse(url=session.url)


@router.post("/confirm-session", response_model=BillingInfoResponse)
async def confirm_checkout_session(
    payload: BillingConfirmRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not settings.STRIPE_SECRET_KEY:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Stripe is not configured on the backend.",
        )

    session = stripe.checkout.Session.retrieve(payload.session_id)
    if not session:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Stripe session not found.")

    reference_user_id = getattr(session, "client_reference_id", None)
    session_metadata = _to_plain_dict(getattr(session, "metadata", None))

    if reference_user_id and str(reference_user_id) != str(current_user.id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="This checkout session does not belong to the current user.")

    if session_metadata.get("user_id") and session_metadata.get("user_id") != str(current_user.id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="This checkout session does not belong to the current user.")

    if getattr(session, "mode", None) != "subscription":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported checkout session type.")

    subscription_id = getattr(session, "subscription", None)
    subscription = stripe.Subscription.retrieve(subscription_id) if subscription_id else None
    subscription_metadata = _to_plain_dict(getattr(subscription, "metadata", None)) if subscription else session_metadata

    plan = str(subscription_metadata.get("plan", session_metadata.get("plan", "pro")))
    billing_cycle = str(subscription_metadata.get("billing_cycle", session_metadata.get("billing_cycle", "monthly")))
    price_id = str(subscription_metadata.get("price_id", session_metadata.get("price_id", "")))

    if not subscription and getattr(session, "payment_status", None) != "paid":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Checkout session is not completed yet.",
        )

    _apply_subscription_state(
        db=db,
        user=current_user,
        plan=plan,
        billing_cycle=billing_cycle,
        subscription_id=getattr(subscription, "id", subscription_id),
        customer_id=getattr(session, "customer", None),
        price_id=price_id,
        status_value=str(getattr(subscription, "status", "active") if subscription else "active"),
        current_period_end=getattr(subscription, "current_period_end", None) if subscription else None,
    )

    return BillingInfoResponse(
        subscription_plan=_enum_text(current_user.subscription_plan),
        subscription_status=current_user.subscription_status,
        subscription_cycle=_enum_text(current_user.subscription_cycle),
        subscription_current_period_end=current_user.subscription_current_period_end,
        is_premium=True,
    )


@router.post("/portal-session", response_model=BillingPortalResponse)
async def create_portal_session(current_user: User = Depends(get_current_user)):
    if not settings.STRIPE_SECRET_KEY:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Stripe is not configured on the backend.",
        )

    customer_id = _get_customer_id(current_user)
    if not customer_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No Stripe customer is attached to this account yet.",
        )

    portal_session = stripe.billing_portal.Session.create(
        customer=customer_id,
        return_url=f"{settings.FRONTEND_URL}/user-dashboard",
    )

    return BillingPortalResponse(url=portal_session.url)


@router.post("/stripe/webhook")
async def stripe_webhook(request: Request, db: Session = Depends(get_db)):
    if not settings.STRIPE_WEBHOOK_SECRET:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Stripe webhook secret is not configured.",
        )

    payload = await request.body()
    signature = request.headers.get("stripe-signature")
    if not signature:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing Stripe signature.")

    try:
        event = stripe.Webhook.construct_event(payload, signature, settings.STRIPE_WEBHOOK_SECRET)
    except stripe.error.SignatureVerificationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid Stripe signature.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid Stripe payload.") from exc

    event_type = event["type"]
    data_object = _to_plain_dict(event["data"]["object"])

    if event_type == "checkout.session.completed":
        metadata = _to_plain_dict(data_object.get("metadata"))
        user = _find_user(db, metadata, data_object.get("customer_email"))
        if user:
            subscription_id = data_object.get("subscription")
            subscription = stripe.Subscription.retrieve(subscription_id) if subscription_id else None
            subscription_metadata = _to_plain_dict(getattr(subscription, "metadata", None)) if subscription else metadata
            _apply_subscription_state(
                db,
                user,
                plan=str(subscription_metadata.get("plan", metadata.get("plan", "pro"))),
                billing_cycle=str(subscription_metadata.get("billing_cycle", metadata.get("billing_cycle", "monthly"))),
                subscription_id=getattr(subscription, "id", subscription_id),
                customer_id=data_object.get("customer"),
                price_id=str(subscription_metadata.get("price_id", metadata.get("price_id", ""))),
                status_value=str(getattr(subscription, "status", "active") if subscription else "active"),
                current_period_end=getattr(subscription, "current_period_end", None) if subscription else None,
            )

    elif event_type in {"customer.subscription.created", "customer.subscription.updated"}:
        metadata = _to_plain_dict(data_object.get("metadata"))
        user = _find_user(db, metadata)
        if user:
            plan = str(metadata.get("plan", user.subscription_plan or "pro"))
            billing_cycle = str(metadata.get("billing_cycle", user.subscription_cycle or "monthly"))
            price_id = None
            items = _to_plain_dict(data_object.get("items")).get("data", [])
            if items:
                price_id = _to_plain_dict(items[0].get("price")).get("id")

            status_value = str(data_object.get("status", "active"))
            if status_value in {"active", "trialing", "past_due"}:
                _apply_subscription_state(
                    db,
                    user,
                    plan=plan,
                    billing_cycle=billing_cycle,
                    subscription_id=str(data_object.get("id")),
                    customer_id=str(data_object.get("customer")) if data_object.get("customer") else user.stripe_customer_id,
                    price_id=price_id,
                    status_value=status_value,
                    current_period_end=data_object.get("current_period_end"),
                )

    elif event_type == "customer.subscription.deleted":
        metadata = _to_plain_dict(data_object.get("metadata"))
        user = _find_user(db, metadata)
        if user:
            _apply_subscription_state(
                db,
                user,
                plan="free",
                billing_cycle=str(metadata.get("billing_cycle", "monthly")),
                subscription_id=str(data_object.get("id")),
                customer_id=str(data_object.get("customer")) if data_object.get("customer") else user.stripe_customer_id,
                price_id=None,
                status_value="canceled",
                current_period_end=data_object.get("current_period_end"),
            )

    return {"received": True}


@router.get("/me", response_model=BillingInfoResponse)
async def get_my_billing(current_user: User = Depends(get_current_user)):
    is_premium = (
        current_user.subscription_plan in {"pro", "enterprise"}
        and current_user.subscription_status == "active"
    )
    return BillingInfoResponse(
        subscription_plan=_enum_text(current_user.subscription_plan),
        subscription_status=current_user.subscription_status,
        subscription_cycle=_enum_text(current_user.subscription_cycle),
        subscription_current_period_end=current_user.subscription_current_period_end,
        is_premium=is_premium,
    )