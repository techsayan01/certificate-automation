#!/usr/bin/env bash
#
# One-command deploy for Certificate Automation → Cloud Run.
#
# Usage:
#   export PROJECT_ID=my-gcp-project
#   export REGION=asia-south1                  # any Cloud Run region
#   export MONGO_URI="mongodb+srv://..."       # Atlas connection string
#   export CANVA_CLIENT_ID="OC-..."            # from canva.com/developers
#   export CANVA_CLIENT_SECRET="cnvca..."
#   export BASE_URL="https://cert-automation-xxxxx.a.run.app"
#       # leave BASE_URL unset on the first run — we'll print the value
#       # Cloud Run assigns and you re-run with it set.
#   export INITIAL_ADMIN_EMAIL="you@example.com"
#   export INITIAL_ADMIN_PASSWORD="ChangeOnFirstLogin!"
#
#   ./infra/deploy.sh
#
# Idempotent. Re-running:
#   • Skips creating things that already exist.
#   • Re-deploys with the latest container image.
#   • Leaves your secrets untouched (use scripts/rotate-secret.sh to rotate).
#
set -euo pipefail

# ── Required env ─────────────────────────────────────────────────────────────
require_var() {
  if [ -z "${!1:-}" ]; then
    echo "✗ ${1} is required" >&2
    exit 1
  fi
}
require_var PROJECT_ID
require_var REGION
require_var MONGO_URI
require_var CANVA_CLIENT_ID
require_var CANVA_CLIENT_SECRET
require_var INITIAL_ADMIN_EMAIL
require_var INITIAL_ADMIN_PASSWORD

SERVICE="${SERVICE:-cert-automation}"
QUEUE_NAME="${QUEUE_NAME:-cert-runs}"
IMAGE="gcr.io/${PROJECT_ID}/${SERVICE}"
CRUN_SA="cert-runtime@${PROJECT_ID}.iam.gserviceaccount.com"
TASKS_SA="cert-tasks@${PROJECT_ID}.iam.gserviceaccount.com"

echo "→ Project   : ${PROJECT_ID}"
echo "→ Region    : ${REGION}"
echo "→ Service   : ${SERVICE}"
echo "→ Image     : ${IMAGE}"
echo

gcloud config set project "${PROJECT_ID}" >/dev/null

# ── 1. Enable APIs (idempotent) ──────────────────────────────────────────────
echo "[1/8] Enabling APIs…"
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  cloudtasks.googleapis.com \
  secretmanager.googleapis.com \
  artifactregistry.googleapis.com \
  iam.googleapis.com \
  --quiet

# ── 2. Service accounts ──────────────────────────────────────────────────────
ensure_sa() {
  local name="$1" display="$2"
  if ! gcloud iam service-accounts describe "${name}@${PROJECT_ID}.iam.gserviceaccount.com" >/dev/null 2>&1; then
    gcloud iam service-accounts create "${name}" --display-name="${display}" --quiet
  fi
}
echo "[2/8] Service accounts…"
ensure_sa cert-runtime "Cert Automation Runtime"
ensure_sa cert-tasks   "Cert Automation Tasks Dispatcher"

# ── 3. Secrets in Secret Manager ─────────────────────────────────────────────
ensure_secret() {
  local name="$1" value="$2"
  if ! gcloud secrets describe "${name}" >/dev/null 2>&1; then
    printf '%s' "${value}" | gcloud secrets create "${name}" --data-file=- --quiet
    echo "  + created ${name}"
  else
    echo "  • ${name} already exists (skipping)"
  fi
}
echo "[3/8] Secrets…"
ensure_secret mongo-uri              "${MONGO_URI}"
ensure_secret canva-client-id        "${CANVA_CLIENT_ID}"
ensure_secret canva-client-secret    "${CANVA_CLIENT_SECRET}"
ensure_secret initial-admin-password "${INITIAL_ADMIN_PASSWORD}"

# Generated keys — only create if missing, never overwrite.
if ! gcloud secrets describe session-secret >/dev/null 2>&1; then
  python3 -c "import secrets; print(secrets.token_urlsafe(48))" \
    | gcloud secrets create session-secret --data-file=- --quiet
  echo "  + created session-secret (generated)"
else
  echo "  • session-secret already exists (skipping)"
fi

if ! gcloud secrets describe encryption-key >/dev/null 2>&1; then
  python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" \
    | gcloud secrets create encryption-key --data-file=- --quiet
  echo "  + created encryption-key (generated)"
else
  echo "  • encryption-key already exists (skipping)"
fi

# ── 4. IAM bindings ──────────────────────────────────────────────────────────
echo "[4/8] IAM…"
for s in mongo-uri session-secret encryption-key initial-admin-password \
         canva-client-id canva-client-secret; do
  gcloud secrets add-iam-policy-binding "${s}" \
    --member="serviceAccount:${CRUN_SA}" \
    --role="roles/secretmanager.secretAccessor" \
    --condition=None \
    --quiet >/dev/null
done

gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${CRUN_SA}" \
  --role="roles/cloudtasks.enqueuer" \
  --condition=None \
  --quiet >/dev/null

gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${TASKS_SA}" \
  --role="roles/run.invoker" \
  --condition=None \
  --quiet >/dev/null

gcloud iam service-accounts add-iam-policy-binding "${TASKS_SA}" \
  --member="serviceAccount:${CRUN_SA}" \
  --role="roles/iam.serviceAccountTokenCreator" \
  --condition=None \
  --quiet >/dev/null

# ── 5. Cloud Tasks queue ─────────────────────────────────────────────────────
echo "[5/8] Cloud Tasks queue…"
if ! gcloud tasks queues describe "${QUEUE_NAME}" --location="${REGION}" >/dev/null 2>&1; then
  gcloud tasks queues create "${QUEUE_NAME}" --location="${REGION}" \
    --max-attempts=3 --max-backoff=1h --min-backoff=30s --quiet
  echo "  + created ${QUEUE_NAME}"
else
  echo "  • ${QUEUE_NAME} already exists (skipping)"
fi

# ── 6. Build + push container ────────────────────────────────────────────────
echo "[6/8] Building container image (this takes 2-3 min)…"
gcloud builds submit \
  --tag "${IMAGE}:latest" \
  --quiet

# ── 7. Deploy to Cloud Run ───────────────────────────────────────────────────
echo "[7/8] Deploying to Cloud Run…"
# BASE_URL is awkward to know before the first deploy. If it's unset, deploy
# with a placeholder; on second run set BASE_URL to the printed URL.
EFFECTIVE_BASE_URL="${BASE_URL:-https://${SERVICE}-placeholder.a.run.app}"

gcloud run deploy "${SERVICE}" \
  --image="${IMAGE}:latest" \
  --region="${REGION}" \
  --platform=managed \
  --service-account="${CRUN_SA}" \
  --allow-unauthenticated \
  --port=8080 \
  --cpu=1 --memory=512Mi \
  --min-instances=0 --max-instances=1 \
  --timeout=3600 \
  --set-env-vars="ENV=production,BASE_URL=${EFFECTIVE_BASE_URL},GCP_PROJECT=${PROJECT_ID},CLOUD_TASKS_LOCATION=${REGION},CLOUD_TASKS_QUEUE=${QUEUE_NAME},CLOUD_TASKS_SA_EMAIL=${TASKS_SA},INITIAL_ADMIN_EMAIL=${INITIAL_ADMIN_EMAIL}" \
  --set-secrets="MONGO_URI=mongo-uri:latest,SESSION_SECRET=session-secret:latest,ENCRYPTION_KEY=encryption-key:latest,INITIAL_ADMIN_PASSWORD=initial-admin-password:latest,CANVA_CLIENT_ID=canva-client-id:latest,CANVA_CLIENT_SECRET=canva-client-secret:latest" \
  --quiet

# ── 8. Print final URL + next steps ──────────────────────────────────────────
SVC_URL=$(gcloud run services describe "${SERVICE}" --region="${REGION}" --format='value(status.url)')

echo
echo "═══════════════════════════════════════════════════════════════"
echo "  ✓ DEPLOYED"
echo "═══════════════════════════════════════════════════════════════"
echo "  Service URL : ${SVC_URL}"
echo "  Login       : ${SVC_URL}/login"
echo "  Admin email : ${INITIAL_ADMIN_EMAIL}"
echo

if [ "${EFFECTIVE_BASE_URL}" != "${SVC_URL}" ]; then
  echo "  ⚠ BASE_URL was a placeholder. Re-run with:"
  echo "     export BASE_URL=\"${SVC_URL}\""
  echo "     ./infra/deploy.sh"
  echo "    — required so OAuth callbacks work correctly."
  echo
fi

echo "  Next:"
echo "    1. Register OAuth redirect URIs"
echo "       Canva  : ${SVC_URL}/oauth/canva/callback"
echo "       Google : ${SVC_URL}/oauth/gmail/callback   (per-festival)"
echo "    2. Sign in, create your first festival, invite a festival user."
echo "    3. (Optional) Wire up auto-deploy with infra/cloudbuild.yaml."
echo "═══════════════════════════════════════════════════════════════"
