# SCHWAK-IT CONSULTANCY
### AI-Powered Tech Consulting · Cybersecurity · Python · Linux · Web Dev · AI/ML · Kubernetes · Data Science
> Powered by Claude (Anthropic) · Payments by Stripe · Deployed on AWS

---

## Architecture

```
CloudFront (CDN)
    └── S3 (Static Frontend)

API Gateway (REST)
    ├── Lambda: schwak-it-consult         (Python 3.12) → SSM (Anthropic key) → Claude API
    ├── Lambda: schwak-it-subscribe       (Python 3.12) → Stripe Checkout + Billing Portal → DynamoDB
    └── Lambda: schwak-it-stripe-webhook  (Python 3.12) → verifies Stripe signature → DynamoDB + SES

SES
    ├── Domain identity + DKIM (schwak-it.com)
    └── Configuration set → SNS topic (bounce/complaint tracking)
```

**Payment flow:** `/subscribe` creates a Stripe Checkout Session and writes
a `pending_payment` row to DynamoDB → customer pays on Stripe's hosted
page → Stripe fires a webhook → `stripe_webhook.py` verifies the signature,
flips the row to `active`, and sends the confirmation email. DynamoDB is
always the source of truth for "is this person actually subscribed" —
the `/subscribe` Lambda never activates anyone directly.

## File Structure

```
schwak-it/
├── template.yaml            # AWS SAM / CloudFormation — full stack
├── samconfig.toml           # SAM deployment config (dev / prod)
├── deploy.sh                # One-command build & deploy script
├── PRODUCTION_ACCESS.md     # Stripe live-mode + SES production access walkthrough
├── src/
│   ├── handler.py           # AI Consultation Lambda (Claude integration)
│   ├── subscribe.py         # Checkout Session + Billing Portal + status/cancel
│   └── stripe_webhook.py    # Verifies Stripe events, syncs DynamoDB, sends emails
├── layer/
│   └── requirements.txt     # anthropic + stripe SDKs → Lambda layer
├── scripts/
│   ├── setup_stripe_products.sh   # Creates Stripe Products/Prices, prints IDs
│   └── check_ses_status.sh        # Checks domain verification + production access status
└── frontend/
    └── index.html            # SCHWAK-IT website — services, pricing, AI chat, checkout
```

---

## Prerequisites

```bash
brew install aws-sam-cli     # macOS — or see AWS docs for your OS
aws configure                # AWS Access Key, Secret, Region (us-east-1), Output (json)
python3 --version             # 3.12+

# For Stripe setup:
brew install stripe/stripe-cli/stripe
stripe login
```

---

## Deploy

### 1. Create Stripe products (test mode is fine to start)
```bash
cd schwak-it/
chmod +x deploy.sh scripts/*.sh
./scripts/setup_stripe_products.sh
```
Paste the printed Price IDs into `samconfig.toml` → `parameter_overrides`.

### 2. Deploy the stack
```bash
./deploy.sh prod          # or: ./deploy.sh dev
```

### 3. Set your secrets in SSM
```bash
aws ssm put-parameter --name /schwak-it/anthropic-api-key \
  --value 'sk-ant-...' --type SecureString --overwrite

aws ssm put-parameter --name /schwak-it/stripe-secret-key \
  --value 'sk_test_...' --type SecureString --overwrite
```

### 4. Register the Stripe webhook
Grab the `StripeWebhookEndpoint` output:
```bash
aws cloudformation describe-stacks --stack-name schwak-it-consultancy-prod \
  --query "Stacks[0].Outputs[?OutputKey=='StripeWebhookEndpoint'].OutputValue" --output text
```
Add it in **Stripe Dashboard → Developers → Webhooks**, selecting
`checkout.session.completed`, `customer.subscription.updated`,
`customer.subscription.deleted`, `invoice.payment_failed`. Then:
```bash
aws ssm put-parameter --name /schwak-it/stripe-webhook-secret \
  --value 'whsec_...' --type SecureString --overwrite
```

### 5. Verify your sending domain for SES
```bash
./scripts/check_ses_status.sh
```
Follow the printed DKIM CNAME instructions, then confirm the SNS
subscription email sent to `AdminNotificationEmail`.

**→ For going live with real charges and real emails, see [`PRODUCTION_ACCESS.md`](./PRODUCTION_ACCESS.md).**
Until then, Stripe test mode + SES sandbox let you test the entire flow
end-to-end for free.

### Frontend-only redeploy
```bash
./deploy.sh frontend
```

---

## API Reference

### POST /consult
```json
{ "message": "How do I harden SSH on Ubuntu?", "history": [] }
→ { "reply": "First principles: disable password auth...", "domain_hint": "linux" }
```

### GET /health
Service status and the 7 supported domains.

### POST /subscribe
```json
{ "name": "Jane Doe", "email": "jane@company.com", "company": "Acme", "plan": "pro" }
→ { "checkout_url": "https://checkout.stripe.com/c/pay/..." }
```
Redirect the browser to `checkout_url`. Stripe handles card entry;
`stripe_webhook.py` activates the subscription once payment succeeds.

### GET /subscribe?email=jane@company.com
Returns `status` (`pending_payment` / `active` / `cancelling` / `past_due`
/ `cancelled`), plan, and monthly usage — synced from Stripe by the webhook.

### POST /subscribe/portal
```json
{ "email": "jane@company.com" }
→ { "portal_url": "https://billing.stripe.com/p/session/..." }
```
Redirect here for self-service plan changes, payment method updates, and
cancellation via Stripe's hosted Billing Portal.

### DELETE /subscribe
```json
{ "email": "jane@company.com" }
```
Cancels at the end of the current billing period via the Stripe API.

### GET /subscribe/plans
Plan metadata (Starter $49, Pro $149, Elite $499).

### POST /webhook/stripe
Stripe calls this directly — signature-verified, not for browser use.

---

## Stack Outputs

| Output | Description |
|--------|-------------|
| `CloudFrontURL` | Your website URL |
| `ApiEndpoint` | API Gateway base URL |
| `StripeWebhookEndpoint` | Paste into Stripe Dashboard webhooks |
| `SESDkimToken1/2/3` | DNS CNAME records for domain verification |
| `FrontendBucketName` | S3 bucket name |
| `CloudFrontDistributionId` | For cache invalidation |
| `SubscribersTableName` | DynamoDB subscribers table |

---

## Cost Estimate (light traffic, post go-live)

| Service | Monthly |
|---------|---------|
| Lambda (3 functions, 1M invocations) | ~$0.60 |
| API Gateway | ~$3.50 |
| S3 + CloudFront | ~$1.00 |
| DynamoDB (on-demand) | ~$0.25 |
| SES | ~$0.10 per 1,000 emails |
| SNS | Negligible |
| SSM Parameters | Free |
| **Anthropic API** | Pay per token |
| **Stripe** | 2.9% + $0.30 per transaction |

---

*SCHWAK-IT CONSULTANCY — We don't consult. We solve.*
