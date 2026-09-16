"""
Provider-agnostic billing interface.

Razorpay (UPI AutoPay subscriptions) is the implementation; another provider
can be added by implementing this interface without touching the rest of the app.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from datetime import datetime


class BillingError(Exception):
    """A billing failure whose message is safe to show to the user."""


class WebhookVerificationError(BillingError):
    """Raised when a webhook signature does not verify."""


@dataclass(frozen=True)
class SubscriptionState:
    """Normalised subscription facts read back from the provider."""

    provider: str
    customer_id: str
    subscription_id: str
    #: The provider's own status string (e.g. Razorpay "authenticated").
    status: str
    plan_price_id: str = ""
    current_period_start: datetime | None = None
    current_period_end: datetime | None = None
    start_at: datetime | None = None
    charge_at: datetime | None = None
    ended_at: datetime | None = None
    short_url: str = ""
    payment_method: str = ""
    raw: dict = field(default_factory=dict, repr=False)


@dataclass(frozen=True)
class WebhookEventData:
    """Normalised, signature-verified webhook payload."""

    event_id: str
    event_type: str
    payload: dict = field(default_factory=dict, repr=False)


class BillingProvider(abc.ABC):
    """Contract every payment provider adapter must satisfy."""

    name: str = "base"

    @abc.abstractmethod
    def create_customer(self, user) -> str:
        """Create (or fetch) the provider customer and return its id."""

    @abc.abstractmethod
    def create_subscription(
        self, *, user, plan, customer_id: str, start_at: datetime | None, offer_id: str = ""
    ) -> SubscriptionState:
        """
        Create a recurring subscription. ``start_at`` delays the first charge (trial,
        free months); ``offer_id`` applies a provider discount offer.
        """

    @abc.abstractmethod
    def fetch_subscription(self, subscription_id: str) -> SubscriptionState:
        """Read authoritative subscription state from the provider."""

    @abc.abstractmethod
    def fetch_payment(self, payment_id: str) -> dict:
        """Read a payment (method, UPI ID or card) from the provider."""

    @abc.abstractmethod
    def cancel_subscription(
        self, subscription_id: str, at_period_end: bool = True
    ) -> SubscriptionState:
        """Cancel a subscription now or at the end of the period."""

    @abc.abstractmethod
    def verify_webhook(
        self, payload: bytes, signature: str, event_id: str = ""
    ) -> WebhookEventData:
        """Verify the signature over the raw body and return the event."""

    @abc.abstractmethod
    def verify_checkout(self, *, payment_id: str, subscription_id: str, signature: str) -> bool:
        """Verify the signature the checkout widget returns to the browser."""

    @abc.abstractmethod
    def state_from_entity(self, entity: dict) -> SubscriptionState:
        """Normalise a subscription object from a webhook or API response."""
