#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════
#  SCHWAK-IT — Stripe Product & Price Setup
#  Creates the 3 subscription products/prices in your Stripe
#  account and prints the Price IDs to paste into samconfig.toml
#  or pass as --parameter-overrides on deploy.
#
#  Requires the Stripe CLI: https://stripe.com/docs/stripe-cli
#  Usage: ./scripts/setup_stripe_products.sh
# ═══════════════════════════════════════════════════════════

set -euo pipefail

CYAN='\033[0;36m'; GREEN='\033[0;32m'; BOLD='\033[1m'; RESET='\033[0m'
log() { echo -e "${CYAN}[STRIPE SETUP]${RESET} $1"; }
ok()  { echo -e "${GREEN}[✓]${RESET} $1"; }

command -v stripe &>/dev/null || { echo "Stripe CLI not found. Install: https://stripe.com/docs/stripe-cli"; exit 1; }

echo -e "${BOLD}Creating SCHWAK-IT subscription products in Stripe...${RESET}"
echo "(Uses whichever Stripe account you're logged into — run 'stripe login' first if needed)"
echo ""

# ── Starter ──
log "Creating Starter product..."
STARTER_PRODUCT=$(stripe products create --name="SCHWAK-IT Starter" \
    --description="50 AI consultations/month · 4 core domains · Email support" \
    -d "metadata[plan]=starter" --format json | jq -r '.id')
STARTER_PRICE=$(stripe prices create --product="$STARTER_PRODUCT" \
    --unit-amount=4900 --currency=usd \
    -d "recurring[interval]=month" --format json | jq -r '.id')
ok "Starter → Product: $STARTER_PRODUCT | Price: $STARTER_PRICE"

# ── Pro ──
log "Creating Pro product..."
PRO_PRODUCT=$(stripe products create --name="SCHWAK-IT Pro" \
    --description="Unlimited AI consultations · All 7 domains · Priority response" \
    -d "metadata[plan]=pro" --format json | jq -r '.id')
PRO_PRICE=$(stripe prices create --product="$PRO_PRODUCT" \
    --unit-amount=14900 --currency=usd \
    -d "recurring[interval]=month" --format json | jq -r '.id')
ok "Pro → Product: $PRO_PRODUCT | Price: $PRO_PRICE"

# ── Elite ──
log "Creating Elite product..."
ELITE_PRODUCT=$(stripe products create --name="SCHWAK-IT Elite" \
    --description="Unlimited AI consultations · Dedicated expert review · Direct engineer access" \
    -d "metadata[plan]=elite" --format json | jq -r '.id')
ELITE_PRICE=$(stripe prices create --product="$ELITE_PRODUCT" \
    --unit-amount=49900 --currency=usd \
    -d "recurring[interval]=month" --format json | jq -r '.id')
ok "Elite → Product: $ELITE_PRODUCT | Price: $ELITE_PRICE"

echo ""
echo -e "${BOLD}${GREEN}════════════════════════════════════════════════${RESET}"
echo -e "${BOLD}Done. Add these to samconfig.toml parameter_overrides,${RESET}"
echo -e "${BOLD}or pass them with --parameter-overrides on deploy:${RESET}"
echo ""
echo "  StripePriceStarter=$STARTER_PRICE"
echo "  StripePricePro=$PRO_PRICE"
echo "  StripePriceElite=$ELITE_PRICE"
echo ""
echo -e "${BOLD}Example samconfig.toml line:${RESET}"
echo "  parameter_overrides = \"Environment=prod StripePriceStarter=$STARTER_PRICE StripePricePro=$PRO_PRICE StripePriceElite=$ELITE_PRICE\""
echo -e "${BOLD}${GREEN}════════════════════════════════════════════════${RESET}"
