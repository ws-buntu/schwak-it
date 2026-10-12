# SCHWAK-IT — Going to Production

Two things stand between this stack and real, paying customers:
**Stripe live mode** (so subscriptions actually charge cards) and
**SES production access** (so confirmation emails reach real inboxes, not
just verified test addresses). This doc walks through both.

---

## Part 1 — Stripe: test mode → live mode

### 1. Create your products & prices
Every Stripe account starts in **test mode**. Build and verify the whole
flow there first — test-mode card `4242 4242 4242 4242` (any future
expiry, any CVC) simulates a successful payment.

```bash
stripe login                              # opens a browser to link your account
./scripts/setup_stripe_products.sh        # creates Starter/Pro/Elite products+prices
```

This prints three Price IDs (`price_...`). Put them in `samconfig.toml`:

```toml
parameter_overrides = "Environment=prod StripePriceStarter=price_xxx StripePricePro=price_yyy StripePriceElite=price_zzz"
```

### 2. Set your Stripe secret key
```bash
aws ssm put-parameter \
  --name /schwak-it/stripe-secret-key \
  --value 'sk_test_...' \
  --type SecureString --overwrite
```

### 3. Deploy, then register the webhook
```bash
./deploy.sh prod
```
Grab the `StripeWebhookEndpoint` value from the stack outputs, then in the
Stripe Dashboard: **Developers → Webhooks → Add endpoint**, paste the URL,
and select these events:
- `checkout.session.completed`
- `customer.subscription.updated`
- `customer.subscription.deleted`
- `invoice.payment_failed`

Copy the **Signing secret** (`whsec_...`) it gives you:
```bash
aws ssm put-parameter \
  --name /schwak-it/stripe-webhook-secret \
  --value 'whsec_...' \
  --type SecureString --overwrite
```

### 4. Test the full loop
Subscribe on the live site with a test card, confirm:
- Checkout redirects back to `?checkout=success`
- The confirmation email arrives (SES sandbox is fine here if your test
  email is verified — see Part 2)
- `GET /subscribe?email=...` shows `"status": "active"`
- Cancelling from the billing portal flips status to `cancelling`, then
  `cancelled` once the period ends

### 5. Flip to live mode
In the Stripe Dashboard, toggle out of test mode (top-right). Live mode
has its own products/prices/keys — re-run `setup_stripe_products.sh`
while logged into live mode, and repeat steps 2–3 with your **live**
secret key (`sk_live_...`) and a **new** webhook endpoint (test and live
webhooks are separate). Update the SSM parameters and price IDs, then
`./deploy.sh prod` again.

Stripe requires a completed business profile (legal entity, bank account,
tax info) before live mode will actually process charges — fill that in
under **Settings → Business settings** if you haven't already.

---

## Part 2 — SES: sandbox → production access

By default every new SES account is in the **sandbox**: you can only send
*to* addresses you've individually verified, capped at 200 emails/day.
That's fine for testing, not for real subscribers.

### 1. Verify your sending domain (already automated)
`template.yaml` provisions an `AWS::SES::EmailIdentity` for your domain
with Easy DKIM. After deploying, add the three DKIM CNAME records to your
DNS — pull them from the stack outputs:

```bash
aws cloudformation describe-stacks --stack-name schwak-it-consultancy-prod \
  --query "Stacks[0].Outputs[?contains(OutputKey,'SESDkim')]" --output table
```

Each output looks like `<token>._domainkey.schwak-it.com -> <token>.dkim.amazonses.com`
— add each as a CNAME record with your DNS provider (Route 53, Cloudflare,
Namecheap, wherever `schwak-it.com` is hosted). Propagation is usually
minutes, occasionally up to 72 hours.

Check status any time:
```bash
./scripts/check_ses_status.sh
```

### 2. Confirm the bounce/complaint SNS subscription
The stack also creates an SNS topic wired to your configuration set for
bounce and complaint tracking (AWS checks for this when reviewing your
production access request). Check the inbox for `AdminNotificationEmail`
(set in `samconfig.toml`, default `admin@schwak-it.com`) for an
**"AWS Notification - Subscription Confirmation"** email and click confirm.

### 3. Request production access
Once the domain shows `VerificationStatus: SUCCESS`:

- Go to **SES Console → Account dashboard → Request production access**
- Mail type: **Transactional**
- Website URL: your CloudFront URL or custom domain
- Describe your use case, e.g.:
  > "SCHWAK-IT CONSULTANCY sends transactional subscription confirmation
  > and cancellation emails to customers who sign up through our website
  > and pay via Stripe Checkout. Volume is expected to be low
  > (tens to low hundreds of emails per day)."
- Describe how you handle bounces/complaints:
  > "Bounce and complaint notifications are routed via an SES
  > configuration set to an SNS topic monitored by our team. We remove
  > hard-bouncing addresses from our sending list."
- Estimate volume conservatively — you can request an increase later

Approval is usually a few hours to one business day. You'll get an email
when it's granted; `./scripts/check_ses_status.sh` will also show
`ProductionAccessEnabled: True` once it's live.

### 4. Recheck after approval
```bash
./scripts/check_ses_status.sh
```
Should show production access granted and your sending quota (starts
around 50,000/day, scales with reputation).

---

## Production checklist

- [ ] Stripe products/prices created, price IDs in `samconfig.toml`
- [ ] Stripe secret key in SSM (`/schwak-it/stripe-secret-key`)
- [ ] Stripe webhook registered, signing secret in SSM
- [ ] Full checkout → webhook → email loop tested end-to-end
- [ ] Stripe account switched to live mode with business profile complete
- [ ] SES domain verified (DKIM CNAMEs added, `VerificationStatus: SUCCESS`)
- [ ] SNS bounce/complaint subscription confirmed
- [ ] SES production access requested and granted
- [ ] Custom domain + ACM certificate on CloudFront (optional but recommended)
- [ ] Billing alarm set on the AWS account (protect against runaway API costs)

Once all of these are checked, you're good to point real customers at the
site.
