"""
Razorpay subscriptions over the REST API.

A subscription with ``start_at`` in the future is a free trial: the customer
approves a UPI AutoPay (or card) mandate now and the first ₹99 charge happens
on ``start_at``, then every month. Razorpay sends webhooks for each charge.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import UTC, datetime

import httpx
from django.conf import settings

from .base import (
    BillingError,
    BillingProvider,
    SubscriptionState,
    WebhookEventData,
    WebhookVerificationError,
)

API_BASE = "https://api.razorpay.com/v1"
TIMEOUT_SECONDS = 15.0
#: Razorpay subscriptions need an end: ten years of monthly charges.
TOTAL_CHARGES = 120


def timestamp(value) -> datetime | None:
    return datetime.fromtimestamp(int(value), tz=UTC) if value else None


class RazorpayBillingProvider(BillingProvider):
    name = "razorpay"

    def __init__(self, *, client: httpx.Client | None = None):
        self._client = client

    @property
    def mock(self) -> bool:
        return settings.RAZORPAY_MOCK

    def create_customer(self, user) -> str:
        if self.mock:
            return f"cust_mock_{user.pk}"
        data = self._request("POST", "/customers", {
            "name": (user.full_name or user.email)[:50],
            "email": user.email,
            "fail_existing": "0",
        })
        return str(data["id"])

    def create_subscription(self, *, user, plan, customer_id, start_at, offer_id=""):
        if self.mock:
            return SubscriptionState(
                provider=self.name,
                customer_id=customer_id,
                subscription_id=f"sub_mock_{uuid.uuid4().hex[:14]}",
                status="created",
                plan_price_id=plan.provider_price_id,
                start_at=start_at,
            )
        if not plan.provider_price_id:
            raise BillingError("Payments aren't set up yet. Please try again later.")
        body = {
            "plan_id": plan.provider_price_id,
            "customer_id": customer_id,
            "total_count": TOTAL_CHARGES,
            "customer_notify": 1,
            "notes": {"user_id": str(user.pk)},
        }
        if start_at:
            body["start_at"] = int(start_at.timestamp())
        if offer_id:
            body["offer_id"] = offer_id
        return self.state_from_entity(self._request("POST", "/subscriptions", body))

    def fetch_subscription(self, subscription_id):
        if self.mock:
            raise BillingError("Test subscriptions can't be fetched.")
        return self.state_from_entity(self._request("GET", f"/subscriptions/{subscription_id}"))

    def fetch_payment(self, payment_id):
        if self.mock:
            raise BillingError("Test payments can't be fetched.")
        return self._request("GET", f"/payments/{payment_id}")

    def cancel_subscription(self, subscription_id, at_period_end=True):
        if self.mock:
            return SubscriptionState(
                provider=self.name, customer_id="", subscription_id=subscription_id,
                status="cancelled",
            )
        entity = self._request(
            "POST",
            f"/subscriptions/{subscription_id}/cancel",
            {"cancel_at_cycle_end": 1 if at_period_end else 0},
        )
        return self.state_from_entity(entity)

    def verify_webhook(self, payload, signature, event_id=""):
        secret = settings.RAZORPAY_WEBHOOK_SECRET
        expected = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
        if not secret or not signature or not hmac.compare_digest(expected, signature):
            raise WebhookVerificationError("Invalid Razorpay webhook signature.")
        try:
            body = json.loads(payload)
        except ValueError as exc:
            raise WebhookVerificationError("Webhook body isn't valid JSON.") from exc
        event_type = str(body.get("event", ""))
        entity = ((body.get("payload") or {}).get("subscription") or {}).get("entity") or {}
        fallback_id = f"{event_type}:{entity.get('id', '')}:{body.get('created_at', '')}"
        return WebhookEventData(
            event_id=event_id or fallback_id, event_type=event_type, payload=body
        )

    def verify_checkout(self, *, payment_id, subscription_id, signature):
        if not (payment_id and subscription_id and signature):
            return False
        message = f"{payment_id}|{subscription_id}".encode()
        expected = hmac.new(
            settings.RAZORPAY_KEY_SECRET.encode(), message, hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(expected, signature)

    def state_from_entity(self, entity):
        return SubscriptionState(
            provider=self.name,
            customer_id=str(entity.get("customer_id") or ""),
            subscription_id=str(entity["id"]),
            status=str(entity.get("status") or ""),
            plan_price_id=str(entity.get("plan_id") or ""),
            current_period_start=timestamp(entity.get("current_start")),
            current_period_end=timestamp(entity.get("current_end")),
            start_at=timestamp(entity.get("start_at")),
            charge_at=timestamp(entity.get("charge_at")),
            ended_at=timestamp(entity.get("ended_at")),
            short_url=str(entity.get("short_url") or ""),
            payment_method=str(entity.get("payment_method") or ""),
            raw=entity,
        )

    def _request(self, method: str, path: str, body: dict | None = None) -> dict:
        if not (settings.RAZORPAY_KEY_ID and settings.RAZORPAY_KEY_SECRET):
            raise BillingError("Payments aren't set up yet. Please try again later.")
        http = self._client or httpx.Client(timeout=TIMEOUT_SECONDS)
        try:
            response = http.request(
                method, f"{API_BASE}{path}", json=body,
                auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET),
            )
        except httpx.HTTPError as exc:
            raise BillingError("Couldn't reach Razorpay. Please try again.") from exc
        finally:
            if self._client is None:
                http.close()
        try:
            data = response.json()
        except ValueError:
            data = {}
        if response.status_code >= 400:
            error = data.get("error") if isinstance(data, dict) else None
            description = error.get("description") if isinstance(error, dict) else ""
            raise BillingError(description or f"Razorpay returned HTTP {response.status_code}.")
        return data
