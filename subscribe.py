"""
SCHWAK-IT CONSULTANCY — Subscription Management Lambda
Routes:
  POST   /subscribe          → create Stripe Checkout Session for a plan
  GET    /subscribe          → get subscription status by email (from DynamoDB, synced by webhook)
  DELETE /subscribe          → cancel subscription (at period end, via Stripe)
  POST   /subscribe/portal   → create a Stripe Billing Portal session (self-service manage/cancel)
  GET    /subscribe/plans    → list available plans

Storage: DynamoDB (source of truth, synced by src/stripe_webhook.py)
Payments: Stripe Checkout + Billing Portal
"""

import json
import os
import re
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb = boto3.resource("dynamodb")
ssm_client = boto3.client("ssm")

TABLE_NAME = os.environ.get("SUBSCRIBERS_TABLE", "schwak-it-subscribers-prod")
SITE_URL = os.environ.get("SITE_URL", "https://schwak-it.com")
STRIPE_KEY_PARAM = os.environ.get("STRIPE_SECRET_KEY_PARAM", "/schwak-it/stripe-secret-key")

# Stripe Price IDs — one per plan. Created in the Stripe Dashboard (see README).
STRIPE_PRICE_IDS = {
    "starter": os.environ.get("STRIPE_PRICE_STARTER", ""),
    "pro": os.environ.get("STRIPE_PRICE_PRO", ""),
    "elite": os.environ.get("STRIPE_PRICE_ELITE", ""),
}

PLANS = {
    "starter": {
        "name": "Starter",
        "price_usd": 49,
        "consultations_per_month": 50,
        "domains": ["cybersecurity", "python", "linux", "web-development"],
        "features": [
            "50 AI consultations / month",
            "4 core domains",
            "Response within 2 hours",
            "Email support",
        ],
    },
    "pro": {
        "name": "Pro",
        "price_usd": 149,
        "consultations_per_month": -1,
        "domains": ["cybersecurity", "python", "linux", "web-development",
                    "artificial-intelligence", "kubernetes", "data-science"],
        "features": [
            "Unlimited AI consultations",
            "All 7 domains",
            "Priority response (< 30 min)",
            "Email + Slack support",
            "Monthly strategy call",
        ],
    },
    "elite": {
        "name": "Elite",
        "price_usd": 499,
        "consultations_per_month": -1,
        "domains": ["cybersecurity", "python", "linux", "web-development",
                    "artificial-intelligence", "kubernetes", "data-science"],
        "features": [
            "Unlimited AI consultations",
            "All 7 domains",
            "Immediate priority response",
            "Dedicated async expert review",
            "Architecture review sessions",
            "Weekly strategy calls",
            "Direct engineer access",
        ],
    },
}

_cached_stripe_key = None


def get_stripe():
    """Lazily import and configure the Stripe SDK (from the Lambda layer) with the SSM-stored key."""
    global _cached_stripe_key
    import stripe

    if not _cached_stripe_key:
        try:
            r = ssm_client.get_parameter(Name=STRIPE_KEY_PARAM, WithDecryption=True)
            _cached_stripe_key = r["Parameter"]["Value"]
        except ClientError as e:
            logger.error(f"Failed to retrieve Stripe key from SSM: {e}")
            raise RuntimeError("Payment configuration error") from e

    stripe.api_key = _cached_stripe_key
    return stripe


def cors_headers() -> dict:
    return {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Headers": "Content-Type,X-Amz-Date,Authorization,X-Api-Key",
        "Access-Control-Allow-Methods": "POST,GET,DELETE,OPTIONS",
        "Content-Type": "application/json",
    }


def resp(status: int, body: dict) -> dict:
    return {"statusCode": status, "headers": cors_headers(), "body": json.dumps(body)}


def validate_email(email: str) -> bool:
    return bool(re.match(r"^[^\s@]+@[^\s@]+\.[^\s@]+$", email))


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_table():
    return dynamodb.Table(TABLE_NAME)


def get_subscriber(email: str) -> Optional[dict]:
    try:
        result = get_table().get_item(Key={"email": email})
        return result.get("Item")
    except ClientError as e:
        logger.error(f"DynamoDB get error: {e}")
        return None


def put_subscriber(record: dict) -> bool:
    try:
        get_table().put_item(Item=record)
        return True
    except ClientError as e:
        logger.error(f"DynamoDB put error: {e}")
        return False


def update_subscriber(email: str, updates: dict) -> bool:
    expressions, names, values = [], {}, {}
    for i, (k, v) in enumerate(updates.items()):
        ph, vp = f"#f{i}", f":v{i}"
        expressions.append(f"{ph} = {vp}")
        names[ph] = k
        values[vp] = v
    try:
        get_table().update_item(
            Key={"email": email},
            UpdateExpression="SET " + ", ".join(expressions),
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values,
        )
        return True
    except ClientError as e:
        logger.error(f"DynamoDB update error: {e}")
        return False


# ── Route: POST /subscribe — create a Stripe Checkout Session ────────────────

def handle_create(body: dict) -> dict:
    email = (body.get("email") or "").strip().lower()
    name = (body.get("name") or "").strip()
    plan = (body.get("plan") or "").strip().lower()
    company = (body.get("company") or "").strip()

    if not email or not validate_email(email):
        return resp(400, {"error": "A valid email address is required."})
    if not name:
        return resp(400, {"error": "Name is required."})
    if plan not in PLANS:
        return resp(400, {"error": f"Invalid plan. Choose from: {', '.join(PLANS.keys())}"})

    price_id = STRIPE_PRICE_IDS.get(plan)
    if not price_id:
        logger.error(f"No Stripe Price ID configured for plan '{plan}'")
        return resp(500, {"error": "This plan isn't available for checkout yet. Contact support."})

    existing = get_subscriber(email)
    if existing and existing.get("status") == "active" and existing.get("plan") == plan:
        return resp(409, {"error": "You are already subscribed to this plan.", "subscription": existing})

    try:
        stripe = get_stripe()

        stripe_customer_id = existing.get("stripe_customer_id") if existing else None
        if not stripe_customer_id:
            found = stripe.Customer.list(email=email, limit=1)
            if found.data:
                stripe_customer_id = found.data[0].id
            else:
                customer = stripe.Customer.create(email=email, name=name, metadata={"company": company})
                stripe_customer_id = customer.id

        session = stripe.checkout.Session.create(
            customer=stripe_customer_id,
            mode="subscription",
            payment_method_types=["card"],
            line_items=[{"price": price_id, "quantity": 1}],
            success_url=f"{SITE_URL}/?checkout=success&plan={plan}",
            cancel_url=f"{SITE_URL}/?checkout=cancelled",
            metadata={"plan": plan, "email": email, "name": name, "company": company},
            subscription_data={"metadata": {"plan": plan, "email": email}},
        )
    except Exception as e:
        logger.error(f"Stripe checkout session creation failed: {e}")
        return resp(502, {"error": "Payment provider error. Please try again shortly."})

    # Pending record so status lookups work right away; webhook flips this to
    # "active" once Stripe confirms payment.
    record = {
        "email": email,
        "name": name,
        "company": company,
        "plan": plan,
        "status": "pending_payment",
        "subscription_id": str(uuid.uuid4()),
        "stripe_customer_id": stripe_customer_id,
        "created_at": existing.get("created_at", now_iso()) if existing else now_iso(),
        "updated_at": now_iso(),
        "plan_details": PLANS[plan],
        "consultations_used_this_month": existing.get("consultations_used_this_month", 0) if existing else 0,
    }
    put_subscriber(record)

    logger.info(f"Checkout session created for {email} -> {plan}")
    return resp(200, {"checkout_url": session.url})


# ── Route: POST /subscribe/portal — Stripe Billing Portal session ────────────

def handle_portal(body: dict) -> dict:
    email = (body.get("email") or "").strip().lower()
    if not email or not validate_email(email):
        return resp(400, {"error": "A valid email address is required."})

    record = get_subscriber(email)
    if not record or not record.get("stripe_customer_id"):
        return resp(404, {"error": "No billing account found for this email."})

    try:
        stripe = get_stripe()
        session = stripe.billing_portal.Session.create(
            customer=record["stripe_customer_id"],
            return_url=f"{SITE_URL}/?portal=return",
        )
    except Exception as e:
        logger.error(f"Stripe billing portal session failed: {e}")
        return resp(502, {"error": "Payment provider error. Please try again shortly."})

    return resp(200, {"portal_url": session.url})


# ── Route: GET /subscribe — status lookup (DynamoDB, synced by webhook) ──────

def handle_get(params: dict) -> dict:
    email = (params.get("email") or "").strip().lower()
    if not email or not validate_email(email):
        return resp(400, {"error": "A valid email query parameter is required."})

    record = get_subscriber(email)
    if not record:
        return resp(404, {"error": "No subscription found for this email."})

    return resp(200, {
        "subscription": {
            "email": record["email"],
            "name": record.get("name"),
            "plan": record["plan"],
            "plan_details": PLANS.get(record["plan"], {}),
            "status": record.get("status"),
            "created_at": record.get("created_at"),
            "updated_at": record.get("updated_at"),
            "consultations_used_this_month": record.get("consultations_used_this_month", 0),
        }
    })


# ── Route: DELETE /subscribe — cancel at period end via Stripe ───────────────

def handle_cancel(body: dict) -> dict:
    email = (body.get("email") or "").strip().lower()
    if not email or not validate_email(email):
        return resp(400, {"error": "A valid email address is required."})

    record = get_subscriber(email)
    if not record:
        return resp(404, {"error": "No subscription found for this email."})
    if record.get("status") == "cancelled":
        return resp(409, {"error": "Subscription is already cancelled."})

    stripe_sub_id = record.get("stripe_subscription_id")
    if stripe_sub_id:
        try:
            stripe = get_stripe()
            stripe.Subscription.modify(stripe_sub_id, cancel_at_period_end=True)
        except Exception as e:
            logger.error(f"Stripe cancellation failed for {email}: {e}")
            return resp(502, {"error": "Payment provider error. Please try again shortly."})
        # Final "cancelled" flip happens via the customer.subscription.updated/deleted
        # webhook; set an interim status here so the UI reflects the change now.
        update_subscriber(email, {"status": "cancelling", "updated_at": now_iso()})
        return resp(200, {"message": "Subscription will cancel at the end of the current billing period."})

    # No Stripe subscription on file (e.g. a manually created record) — flip directly.
    update_subscriber(email, {"status": "cancelled", "cancelled_at": now_iso(), "updated_at": now_iso()})
    return resp(200, {"message": "Subscription cancelled."})


def handle_plans() -> dict:
    return resp(200, {"plans": PLANS})


def lambda_handler(event: dict, context) -> dict:
    method = event.get("httpMethod", "GET")
    path = event.get("path", "/subscribe")

    if method == "OPTIONS":
        return {"statusCode": 200, "headers": cors_headers(), "body": ""}

    if path == "/subscribe/plans" and method == "GET":
        return handle_plans()

    if path == "/subscribe/portal" and method == "POST":
        try:
            body = json.loads(event.get("body") or "{}")
        except json.JSONDecodeError:
            return resp(400, {"error": "Invalid JSON body."})
        return handle_portal(body)

    if method == "POST":
        try:
            body = json.loads(event.get("body") or "{}")
        except json.JSONDecodeError:
            return resp(400, {"error": "Invalid JSON body."})
        return handle_create(body)

    if method == "GET":
        params = event.get("queryStringParameters") or {}
        return handle_get(params)

    if method == "DELETE":
        try:
            body = json.loads(event.get("body") or "{}")
        except json.JSONDecodeError:
            return resp(400, {"error": "Invalid JSON body."})
        return handle_cancel(body)

    return resp(404, {"error": "Route not found."})
