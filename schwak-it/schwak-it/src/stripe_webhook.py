"""
SCHWAK-IT CONSULTANCY — Stripe Webhook Handler Lambda
Route: POST /webhook/stripe

Verifies the Stripe signature, then syncs subscription state into DynamoDB
and fires the SES confirmation/cancellation emails. This is the single
source of truth for "is this subscriber actually paying" — the /subscribe
Lambda only ever creates *pending* records; this webhook is what activates
or cancels them.

Events handled:
  checkout.session.completed   → mark subscriber active, store Stripe IDs, send welcome email
  customer.subscription.updated → sync status (active / past_due / cancelling)
  customer.subscription.deleted → mark cancelled, send cancellation email
  invoice.payment_failed        → mark past_due
"""

import json
import os
import logging
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb = boto3.resource("dynamodb")
ses_client = boto3.client("ses")
ssm_client = boto3.client("ssm")

TABLE_NAME = os.environ.get("SUBSCRIBERS_TABLE", "schwak-it-subscribers-prod")
SENDER_EMAIL = os.environ.get("SENDER_EMAIL", "noreply@schwak-it.com")
STRIPE_KEY_PARAM = os.environ.get("STRIPE_SECRET_KEY_PARAM", "/schwak-it/stripe-secret-key")
STRIPE_WEBHOOK_SECRET_PARAM = os.environ.get("STRIPE_WEBHOOK_SECRET_PARAM", "/schwak-it/stripe-webhook-secret")

# Reverse lookup: Stripe Price ID -> our internal plan key
PRICE_TO_PLAN = {
    os.environ.get("STRIPE_PRICE_STARTER", ""): "starter",
    os.environ.get("STRIPE_PRICE_PRO", ""): "pro",
    os.environ.get("STRIPE_PRICE_ELITE", ""): "elite",
}

PLAN_NAMES = {"starter": "Starter", "pro": "Pro", "elite": "Elite"}

_cached_stripe_key = None
_cached_webhook_secret = None


def get_table():
    return dynamodb.Table(TABLE_NAME)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_ssm_param(name: str) -> str:
    r = ssm_client.get_parameter(Name=name, WithDecryption=True)
    return r["Parameter"]["Value"]


def get_stripe():
    global _cached_stripe_key
    import stripe
    if not _cached_stripe_key:
        _cached_stripe_key = get_ssm_param(STRIPE_KEY_PARAM)
    stripe.api_key = _cached_stripe_key
    return stripe


def get_webhook_secret() -> str:
    global _cached_webhook_secret
    if not _cached_webhook_secret:
        _cached_webhook_secret = get_ssm_param(STRIPE_WEBHOOK_SECRET_PARAM)
    return _cached_webhook_secret


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
        logger.error(f"DynamoDB update error for {email}: {e}")
        return False


def get_subscriber_by_customer(stripe_customer_id: str):
    """Scan for the subscriber matching this Stripe customer ID.
    Fine at this table's expected scale (SaaS consultancy signups, not millions of rows).
    For very high volume, add a GSI on stripe_customer_id instead."""
    try:
        result = get_table().scan(
            FilterExpression="stripe_customer_id = :c",
            ExpressionAttributeValues={":c": stripe_customer_id},
            Limit=1,
        )
        items = result.get("Items", [])
        return items[0] if items else None
    except ClientError as e:
        logger.error(f"DynamoDB scan error for customer {stripe_customer_id}: {e}")
        return None


def send_email(to_email: str, subject: str, html_body: str, text_body: str) -> None:
    try:
        ses_client.send_email(
            Source=f"SCHWAK-IT CONSULTANCY <{SENDER_EMAIL}>",
            Destination={"ToAddresses": [to_email]},
            Message={
                "Subject": {"Data": subject, "Charset": "UTF-8"},
                "Body": {
                    "Html": {"Charset": "UTF-8", "Data": html_body},
                    "Text": {"Charset": "UTF-8", "Data": text_body},
                },
            },
        )
        logger.info(f"Email sent to {to_email}: {subject}")
    except ClientError as e:
        logger.warning(f"SES send failed for {to_email}: {e}")


def welcome_email(email: str, name: str, plan_name: str, price_usd: int) -> None:
    html = f"""<!DOCTYPE html><html><head><meta charset="UTF-8"/></head>
<body style="background:#050608;color:#e8eaf0;font-family:'DM Sans',sans-serif;padding:40px;">
  <div style="max-width:560px;margin:0 auto;">
    <h1 style="font-size:2rem;color:#00e5ff;letter-spacing:0.1em;margin-bottom:0.5rem;">SCHWAK-IT</h1>
    <p style="color:#8892a4;font-size:0.8rem;letter-spacing:0.2em;text-transform:uppercase;margin-bottom:2rem;">CONSULTANCY</p>
    <hr style="border:none;border-top:1px solid #1e2430;margin-bottom:2rem;"/>
    <p style="font-size:1.1rem;">Hi {name},</p>
    <p style="color:#8892a4;line-height:1.7;margin:1rem 0;">
      Payment confirmed. You're now on the <strong style="color:#00e5ff;">{plan_name}</strong> plan
      at <strong style="color:#e8eaf0;">${price_usd}/month</strong>. First principles: go build something.
    </p>
    <a href="{os.environ.get('SITE_URL', 'https://schwak-it.com')}/#consult"
       style="display:inline-block;background:#00e5ff;color:#050608;font-weight:700;text-decoration:none;
              padding:0.8rem 1.8rem;margin-top:1rem;font-family:monospace;font-size:0.8rem;
              letter-spacing:0.1em;text-transform:uppercase;">START CONSULTING →</a>
    <hr style="border:none;border-top:1px solid #1e2430;margin-top:2rem;"/>
    <p style="color:#4a5568;font-size:0.75rem;margin-top:1rem;">
      SCHWAK-IT CONSULTANCY · We don't consult. We solve.<br/>
      Manage or cancel your subscription any time from your account.
    </p>
  </div>
</body></html>"""
    text = (
        f"Hi {name},\n\nPayment confirmed. You're on the {plan_name} plan at ${price_usd}/month.\n\n"
        "We don't consult. We solve.\n— SCHWAK-IT"
    )
    send_email(email, f"SCHWAK-IT — {plan_name} Plan Activated", html, text)


def cancellation_email(email: str, name: str) -> None:
    text = (
        f"Hi {name},\n\nYour SCHWAK-IT subscription has been cancelled.\n"
        "Access remains active until the end of your current billing period.\n\n"
        "Come back any time — the engine will be here.\n\n— SCHWAK-IT CONSULTANCY"
    )
    send_email(email, "SCHWAK-IT — Subscription Cancelled", f"<p>{text.replace(chr(10), '<br/>')}</p>", text)


# ── Stripe event handlers ─────────────────────────────────────────────────────

def handle_checkout_completed(session_obj: dict) -> None:
    meta = session_obj.get("metadata", {}) or {}
    email = meta.get("email") or session_obj.get("customer_details", {}).get("email")
    if not email:
        logger.error("checkout.session.completed had no email in metadata; skipping")
        return

    plan = meta.get("plan", "")
    name = meta.get("name", "")
    stripe_customer_id = session_obj.get("customer")
    stripe_subscription_id = session_obj.get("subscription")

    update_subscriber(email, {
        "status": "active",
        "plan": plan,
        "stripe_customer_id": stripe_customer_id,
        "stripe_subscription_id": stripe_subscription_id,
        "updated_at": now_iso(),
        "billing_cycle_start": now_iso(),
    })

    plan_name = PLAN_NAMES.get(plan, plan.title())
    price_map = {"starter": 49, "pro": 149, "elite": 499}
    welcome_email(email, name or email, plan_name, price_map.get(plan, 0))
    logger.info(f"Activated subscription for {email} -> {plan}")


def handle_subscription_updated(sub_obj: dict) -> None:
    stripe_customer_id = sub_obj.get("customer")
    subscriber = get_subscriber_by_customer(stripe_customer_id)
    if not subscriber:
        logger.warning(f"No subscriber found for Stripe customer {stripe_customer_id}")
        return

    email = subscriber["email"]
    status = sub_obj.get("status")  # active, past_due, canceled, unpaid, trialing, etc.
    cancel_at_period_end = sub_obj.get("cancel_at_period_end", False)

    # Map Stripe's status vocabulary onto ours
    if status == "active" and cancel_at_period_end:
        our_status = "cancelling"
    elif status == "active":
        our_status = "active"
    elif status in ("past_due", "unpaid"):
        our_status = "past_due"
    elif status in ("canceled", "incomplete_expired"):
        our_status = "cancelled"
    else:
        our_status = status

    # Keep plan in sync if the price/item changed (upgrade/downgrade)
    items = sub_obj.get("items", {}).get("data", [])
    updates = {"status": our_status, "updated_at": now_iso()}
    if items:
        price_id = items[0].get("price", {}).get("id")
        plan = PRICE_TO_PLAN.get(price_id)
        if plan:
            updates["plan"] = plan

    update_subscriber(email, updates)
    logger.info(f"Synced subscription for {email}: status={our_status}")


def handle_subscription_deleted(sub_obj: dict) -> None:
    stripe_customer_id = sub_obj.get("customer")
    subscriber = get_subscriber_by_customer(stripe_customer_id)
    if not subscriber:
        logger.warning(f"No subscriber found for Stripe customer {stripe_customer_id}")
        return

    email = subscriber["email"]
    update_subscriber(email, {"status": "cancelled", "cancelled_at": now_iso(), "updated_at": now_iso()})
    cancellation_email(email, subscriber.get("name", email))
    logger.info(f"Subscription fully cancelled for {email}")


def handle_payment_failed(invoice_obj: dict) -> None:
    stripe_customer_id = invoice_obj.get("customer")
    subscriber = get_subscriber_by_customer(stripe_customer_id)
    if not subscriber:
        return
    update_subscriber(subscriber["email"], {"status": "past_due", "updated_at": now_iso()})
    logger.info(f"Marked {subscriber['email']} as past_due after failed payment")


# ── Lambda entrypoint ──────────────────────────────────────────────────────────

def lambda_handler(event: dict, context) -> dict:
    headers = event.get("headers") or {}
    # API Gateway lower-cases header names in some invocation types; check both
    sig_header = headers.get("Stripe-Signature") or headers.get("stripe-signature")
    raw_body = event.get("body") or ""

    if event.get("isBase64Encoded"):
        import base64
        raw_body = base64.b64decode(raw_body).decode("utf-8")

    if not sig_header:
        logger.error("Missing Stripe-Signature header")
        return {"statusCode": 400, "body": json.dumps({"error": "Missing signature"})}

    try:
        stripe = get_stripe()
        webhook_secret = get_webhook_secret()
        stripe_event = stripe.Webhook.construct_event(raw_body, sig_header, webhook_secret)
    except ValueError:
        logger.error("Invalid webhook payload")
        return {"statusCode": 400, "body": json.dumps({"error": "Invalid payload"})}
    except Exception as e:
        # Covers stripe.error.SignatureVerificationError and SSM/config errors
        logger.error(f"Webhook signature verification failed: {e}")
        return {"statusCode": 400, "body": json.dumps({"error": "Invalid signature"})}

    event_type = stripe_event["type"]
    data_obj = stripe_event["data"]["object"]
    logger.info(f"Received Stripe event: {event_type}")

    try:
        if event_type == "checkout.session.completed":
            handle_checkout_completed(data_obj)
        elif event_type == "customer.subscription.updated":
            handle_subscription_updated(data_obj)
        elif event_type == "customer.subscription.deleted":
            handle_subscription_deleted(data_obj)
        elif event_type == "invoice.payment_failed":
            handle_payment_failed(data_obj)
        else:
            logger.info(f"Unhandled event type: {event_type} (ignored)")
    except Exception as e:
        # Log but still return 200 — Stripe retries on non-2xx, and a processing
        # bug shouldn't cause Stripe to hammer this endpoint indefinitely.
        logger.error(f"Error processing {event_type}: {e}")

    return {"statusCode": 200, "body": json.dumps({"received": True})}
