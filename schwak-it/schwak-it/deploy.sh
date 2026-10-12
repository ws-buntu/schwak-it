#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════
#  SCHWAK-IT CONSULTANCY — Build & Deploy Script
#  Usage:
#    ./deploy.sh              → deploy to prod
#    ./deploy.sh dev          → deploy to dev
#    ./deploy.sh frontend     → sync frontend to S3 only
# ═══════════════════════════════════════════════════════════

set -euo pipefail

ENV="${1:-prod}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

log()  { echo -e "${CYAN}[SCHWAK-IT]${RESET} $1"; }
ok()   { echo -e "${GREEN}[✓]${RESET} $1"; }
warn() { echo -e "${YELLOW}[!]${RESET} $1"; }
err()  { echo -e "${RED}[✗]${RESET} $1"; exit 1; }

echo -e "${BOLD}"
echo "╔══════════════════════════════════════════╗"
echo "║      SCHWAK-IT CONSULTANCY DEPLOY        ║"
echo "║ Cyber · Python · Linux · Web · AI · K8s  ║"
echo "╚══════════════════════════════════════════╝"
echo -e "${RESET}"

command -v sam   &>/dev/null || err "AWS SAM CLI not installed. brew install aws-sam-cli"
command -v aws   &>/dev/null || err "AWS CLI not installed."
command -v pip3  &>/dev/null || err "pip3 not installed."

if [[ "$ENV" == "frontend" ]]; then
    log "Syncing frontend to S3..."
    STACK_NAME="schwak-it-consultancy-prod"
    BUCKET=$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" \
        --query "Stacks[0].Outputs[?OutputKey=='FrontendBucketName'].OutputValue" --output text)
    CF_ID=$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" \
        --query "Stacks[0].Outputs[?OutputKey=='CloudFrontDistributionId'].OutputValue" --output text)
    aws s3 sync "$SCRIPT_DIR/frontend/" "s3://$BUCKET/" --delete
    aws cloudfront create-invalidation --distribution-id "$CF_ID" --paths "/*" > /dev/null
    ok "Frontend deployed! CloudFront cache invalidated."
    exit 0
fi

log "Installing Lambda layer dependencies..."
mkdir -p "$SCRIPT_DIR/layer/python"
pip3 install -r "$SCRIPT_DIR/layer/requirements.txt" -t "$SCRIPT_DIR/layer/python/" --quiet
ok "Layer dependencies installed."

log "Building SAM application..."
sam build --config-env "$ENV" --cached --parallel
ok "SAM build complete."

log "Deploying to AWS ($ENV environment)..."
sam deploy --config-env "$ENV" --no-fail-on-empty-changeset
ok "SAM deploy complete."

STACK_NAME="schwak-it-consultancy-${ENV}"
API_ENDPOINT=$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" \
    --query "Stacks[0].Outputs[?OutputKey=='ApiEndpoint'].OutputValue" --output text)
CF_URL=$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" \
    --query "Stacks[0].Outputs[?OutputKey=='CloudFrontURL'].OutputValue" --output text)
BUCKET=$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" \
    --query "Stacks[0].Outputs[?OutputKey=='FrontendBucketName'].OutputValue" --output text)
CF_ID=$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" \
    --query "Stacks[0].Outputs[?OutputKey=='CloudFrontDistributionId'].OutputValue" --output text)

log "Injecting API endpoint into frontend..."
sed "s|__API_ENDPOINT__|${API_ENDPOINT}|g" "$SCRIPT_DIR/frontend/index.html" > /tmp/index_built.html
cp /tmp/index_built.html "$SCRIPT_DIR/frontend/index.html"
ok "API endpoint injected."

log "Uploading frontend to S3 ($BUCKET)..."
aws s3 sync "$SCRIPT_DIR/frontend/" "s3://$BUCKET/" --delete \
    --cache-control "public, max-age=31536000" --exclude "*.html"
aws s3 sync "$SCRIPT_DIR/frontend/" "s3://$BUCKET/" --delete \
    --exclude "*" --include "*.html" --cache-control "no-cache, no-store, must-revalidate"
ok "Frontend uploaded."

log "Invalidating CloudFront cache..."
aws cloudfront create-invalidation --distribution-id "$CF_ID" --paths "/*" > /dev/null
ok "Cache invalidated."

warn "Don't forget to set your Anthropic API key in SSM:"
echo -e "  ${YELLOW}aws ssm put-parameter \\${RESET}"
echo -e "  ${YELLOW}  --name /schwak-it/anthropic-api-key \\${RESET}"
echo -e "  ${YELLOW}  --value 'sk-ant-...' \\${RESET}"
echo -e "  ${YELLOW}  --type SecureString \\${RESET}"
echo -e "  ${YELLOW}  --overwrite${RESET}"

warn "And your Stripe secret key + webhook signing secret:"
echo -e "  ${YELLOW}aws ssm put-parameter --name /schwak-it/stripe-secret-key --value 'sk_test_...' --type SecureString --overwrite${RESET}"
echo -e "  ${YELLOW}aws ssm put-parameter --name /schwak-it/stripe-webhook-secret --value 'whsec_...' --type SecureString --overwrite${RESET}"

warn "Full Stripe + SES production go-live steps: see PRODUCTION_ACCESS.md"

echo ""
echo -e "${BOLD}${GREEN}════════════════════════════════════════${RESET}"
echo -e "${BOLD}  SCHWAK-IT is LIVE${RESET}"
echo -e "  Website : ${CYAN}${CF_URL}${RESET}"
echo -e "  API     : ${CYAN}${API_ENDPOINT}${RESET}"
echo -e "${BOLD}${GREEN}════════════════════════════════════════${RESET}"
