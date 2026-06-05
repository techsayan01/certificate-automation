# Cloud Run deployment — Phase 3

End-to-end recipe for deploying the certificate automation service on
GCP. Run these once per GCP project; CI then takes over via Cloud Build.

## 1. Prerequisites

- A GCP project with billing enabled
- `gcloud` CLI authenticated as a user with `Owner` (or fine-grained
  equivalents)
- A MongoDB Atlas cluster reachable from Cloud Run egress

```bash
export PROJECT_ID=my-cert-automation
export REGION=asia-south1
gcloud config set project $PROJECT_ID
```

## 2. Enable APIs

```bash
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  cloudtasks.googleapis.com \
  secretmanager.googleapis.com \
  artifactregistry.googleapis.com \
  iam.googleapis.com
```

## 3. Secrets in Secret Manager

```bash
# Mongo Atlas connection string
echo -n "mongodb+srv://..." | gcloud secrets create mongo-uri --data-file=-

# Random session secret
python -c "import secrets; print(secrets.token_urlsafe(48))" \
  | gcloud secrets create session-secret --data-file=-

# Fernet encryption key
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" \
  | gcloud secrets create encryption-key --data-file=-

# Bootstrap admin password (only used on first boot)
echo -n "ChangeOnFirstLogin!" \
  | gcloud secrets create initial-admin-password --data-file=-

# Canva integration credentials
echo -n "OC-XXXXXXX"     | gcloud secrets create canva-client-id     --data-file=-
echo -n "cnvca-XXXXXXX"  | gcloud secrets create canva-client-secret --data-file=-
```

## 4. Service accounts

The Cloud Run service needs:
- Read access to all the secrets above
- Permission to create Cloud Tasks

```bash
# 4a. Cloud Run runtime SA
gcloud iam service-accounts create cert-runtime \
  --display-name="Certificate Automation Runtime"

CRUN_SA=cert-runtime@${PROJECT_ID}.iam.gserviceaccount.com

# Grant secret access
for s in mongo-uri session-secret encryption-key initial-admin-password \
         canva-client-id canva-client-secret; do
  gcloud secrets add-iam-policy-binding $s \
    --member="serviceAccount:${CRUN_SA}" \
    --role="roles/secretmanager.secretAccessor"
done

# Cloud Tasks enqueue permission
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:${CRUN_SA}" \
  --role="roles/cloudtasks.enqueuer"

# 4b. Cloud Tasks dispatcher SA — used to attach OIDC tokens to outbound
#     tasks so the worker endpoint can authenticate the caller.
gcloud iam service-accounts create cert-tasks \
  --display-name="Certificate Automation Tasks Dispatcher"

TASKS_SA=cert-tasks@${PROJECT_ID}.iam.gserviceaccount.com

# The dispatcher SA needs to be invokable by Cloud Tasks
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:${TASKS_SA}" \
  --role="roles/run.invoker"

# The runtime SA needs the impersonation right
gcloud iam service-accounts add-iam-policy-binding $TASKS_SA \
  --member="serviceAccount:${CRUN_SA}" \
  --role="roles/iam.serviceAccountTokenCreator"
```

## 5. Cloud Tasks queue

```bash
gcloud tasks queues create cert-runs \
  --location=$REGION \
  --max-attempts=3 \
  --max-backoff=1h \
  --min-backoff=30s
```

## 6. First-time deploy

```bash
gcloud builds submit \
  --config=infra/cloudbuild.yaml \
  --substitutions=_REGION=$REGION,_SERVICE=cert-automation,_BASE_URL=https://cert-automation.example.com,_IMAGE=gcr.io/$PROJECT_ID/cert-automation
```

Bind the runtime SA after the first deploy:

```bash
gcloud run services update cert-automation \
  --region=$REGION \
  --service-account=${CRUN_SA}
```

## 7. Wire up GitHub auto-deploy

In the Cloud Console:
1. Cloud Build → Triggers → Create trigger
2. Source = GitHub repo, branch = `^main$`
3. Configuration = Cloud Build config file → `/infra/cloudbuild.yaml`
4. Substitutions: override `_BASE_URL` to your actual hostname

From here on, every push to `main` builds and deploys automatically.

## 8. Verify

```bash
SVC_URL=$(gcloud run services describe cert-automation --region=$REGION --format='value(status.url)')

curl -sf $SVC_URL/healthz   # → {"status":"ok"}
open $SVC_URL/login         # sign in with the bootstrap admin
```

## OAuth redirect URIs to register

After the service URL is known, register these callback URLs:

- **Google Cloud Console** (per festival's OAuth client):
  `${BASE_URL}/oauth/gmail/callback`
- **Canva Developer Portal** (Cert-Automate integration):
  `${BASE_URL}/oauth/canva/callback`

Both can include `http://127.0.0.1:8000/...` as a second entry for local dev.

## Cost ballpark

For a festival running ~100 certificates per month:

| Component         | Monthly cost (approx) |
|-------------------|-----------------------|
| Cloud Run         | $0 (within free tier) |
| Cloud Tasks       | $0 (within free tier) |
| Secret Manager    | $0.06 per secret × 6 ≈ $0.40 |
| Cloud Build       | $0 (120 builds/day free) |
| Mongo Atlas (M0)  | $0 (free tier)        |
| **Total**         | **~$0.50/month**      |
