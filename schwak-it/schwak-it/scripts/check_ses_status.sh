#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════
#  SCHWAK-IT — SES Production Access Checker
#  Run this after `./deploy.sh prod` to see exactly what's
#  left before you can send email to unverified addresses.
#
#  Usage: ./scripts/check_ses_status.sh [region]
# ═══════════════════════════════════════════════════════════

set -euo pipefail

REGION="${1:-us-east-1}"
DOMAIN="${SES_DOMAIN:-schwak-it.com}"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

log()  { echo -e "${CYAN}[SES CHECK]${RESET} $1"; }
ok()   { echo -e "${GREEN}[✓]${RESET} $1"; }
warn() { echo -e "${YELLOW}[!]${RESET} $1"; }
bad()  { echo -e "${RED}[✗]${RESET} $1"; }

echo -e "${BOLD}"
echo "╔══════════════════════════════════════════╗"
echo "║   SCHWAK-IT — SES PRODUCTION READINESS   ║"
echo "╚══════════════════════════════════════════╝"
echo -e "${RESET}"

# 1. Account-level sending status (sandbox vs production)
log "Checking account sending status..."
ENABLED=$(aws sesv2 get-account --region "$REGION" --query "SendingEnabled" --output text 2>/dev/null || echo "unknown")
PROD_ACCESS=$(aws sesv2 get-account --region "$REGION" --query "ProductionAccessEnabled" --output text 2>/dev/null || echo "unknown")

if [[ "$PROD_ACCESS" == "True" ]]; then
    ok "Production access: GRANTED — you can send to any address."
else
    warn "Production access: NOT granted — you're in the SES sandbox."
    warn "  In sandbox mode you can only send to verified email addresses,"
    warn "  and volume is capped at 200 emails/day, 1/second."
fi

# 2. Sending quota
log "Checking sending quota..."
aws sesv2 get-account --region "$REGION" \
    --query "{Max24Hour:SendQuota.Max24HourSend, MaxSendRate:SendQuota.MaxSendRate, SentLast24h:SendQuota.SentLast24Hours}" \
    --output table 2>/dev/null || warn "Could not fetch quota (check credentials/region)."

# 3. Domain identity verification status
log "Checking domain identity for '$DOMAIN'..."
VERIFICATION=$(aws sesv2 get-email-identity --email-identity "$DOMAIN" --region "$REGION" \
    --query "VerificationStatus" --output text 2>/dev/null || echo "NOT_FOUND")

if [[ "$VERIFICATION" == "SUCCESS" ]]; then
    ok "Domain '$DOMAIN' is verified."
elif [[ "$VERIFICATION" == "NOT_FOUND" ]]; then
    bad "Domain '$DOMAIN' identity not found. Did the stack deploy the SESDomainIdentity resource?"
else
    warn "Domain '$DOMAIN' verification status: $VERIFICATION"
    warn "  DNS records may not have propagated yet. Check with: dig CNAME <token>._domainkey.$DOMAIN"
fi

# 4. DKIM status
log "Checking DKIM signing status..."
DKIM_STATUS=$(aws sesv2 get-email-identity --email-identity "$DOMAIN" --region "$REGION" \
    --query "DkimAttributes.Status" --output text 2>/dev/null || echo "NOT_FOUND")

if [[ "$DKIM_STATUS" == "SUCCESS" ]]; then
    ok "DKIM: verified and active."
else
    warn "DKIM status: $DKIM_STATUS"
    warn "  Grab the 3 CNAME records from the stack outputs (SESDkimToken1/2/3)"
    warn "  and add them at your DNS provider. Propagation can take up to 72h."
fi

echo ""
if [[ "$PROD_ACCESS" != "True" ]]; then
    echo -e "${BOLD}${YELLOW}Next step:${RESET} request production access."
    echo "  Console path: SES > Account dashboard > Request production access"
    echo "  Full walkthrough: see PRODUCTION_ACCESS.md in this repo."
fi
