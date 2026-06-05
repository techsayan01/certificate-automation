# Go-live runbook

End-to-end checklist for shipping certificate-automation to production.
Budget about **2 hours** start to finish if you've never touched the
GCP project before; 30 minutes if everything is already set up.

---

## 0. Merge the open PRs

In this order, on `main`:

1. [`feat/multi-festival-canva-api`](https://github.com/techsayan01/certificate-automation/pulls?q=is%3Apr+head%3Afeat%2Fmulti-festival-canva-api)
   CLI Canva API + multi-cert per recipient. Standalone — keeps the CLI
   working for one-off debugging after we go live.
2. [`feat/saas-backend-phase1`](https://github.com/techsayan01/certificate-automation/pulls?q=is%3Apr+head%3Afeat%2Fsaas-backend-phase1)
   Backend skeleton: auth + admin festival CRUD.
3. [`feat/saas-backend-phase2`](https://github.com/techsayan01/certificate-automation/pulls?q=is%3Apr+head%3Afeat%2Fsaas-backend-phase2)
   Festival user role + Gmail/Canva OAuth + templates CRUD + admin users.
4. [`feat/saas-backend-phase3`](https://github.com/techsayan01/certificate-automation/pulls?q=is%3Apr+head%3Afeat%2Fsaas-backend-phase3)
   Pipeline services + send/run dashboard + Cloud Build YAML.
5. [`feat/saas-backend-phase4-status-templates`](https://github.com/techsayan01/certificate-automation/pulls?q=is%3Apr+head%3Afeat%2Fsaas-backend-phase4-status-templates)
   Templates keyed by status only.
6. `feat/go-live` (this PR) — deploy.sh, runbook, cloudbuild fixes.

Each PR is independent enough that you can pause between them if anything
looks off in review.

---

## 1. Prerequisites

You'll need:

- **GCP project** with billing enabled (free tier is enough — see
  cost ballpark in `infra/README.md`)
- **MongoDB Atlas** cluster — the M0 free tier is fine for one festival
- **Canva integration** credentials from canva.com/developers (the
  `Cert-Automate` integration we set up during the CLI work — already
  approved for `design:meta:read` + `design:content:*`)
- **Local tools**: `gcloud` CLI, Python 3 with `cryptography` installed
  (the deploy script uses both)

Authenticate gcloud as a user with `Owner` on the project (or
fine-grained roles for Cloud Run / Cloud Build / Secret Manager / IAM
admin):

```bash
gcloud auth login
gcloud auth application-default login
```

---

## 2. One-command deploy

```bash
export PROJECT_ID=my-cert-automation
export REGION=asia-south1                    # or us-central1, eu-west1, etc.
export MONGO_URI="mongodb+srv://USER:PASS@cluster0.xxxxx.mongodb.net/?retryWrites=true&w=majority"
export CANVA_CLIENT_ID="OC-AZ5Z5vWw1KJn"
export CANVA_CLIENT_SECRET="cnvca..."        # the secret from the CLI work
export INITIAL_ADMIN_EMAIL="you@example.com"
export INITIAL_ADMIN_PASSWORD="ChangeOnFirstLogin!"

./infra/deploy.sh
```

The script:

1. Enables Cloud Run, Cloud Build, Cloud Tasks, Secret Manager, IAM,
   Artifact Registry
2. Creates two service accounts (`cert-runtime`, `cert-tasks`) and
   binds them to the right roles
3. Pushes your secrets into Secret Manager — generates SESSION_SECRET
   and ENCRYPTION_KEY for you
4. Creates the `cert-runs` Cloud Tasks queue
5. Builds the container via Cloud Build
6. Deploys to Cloud Run with all the env/secret bindings wired up
7. Prints the service URL + next steps

On first run, `BASE_URL` is a placeholder. The script prints the actual
URL Cloud Run assigned — re-run with `export BASE_URL="https://..."` so
OAuth callbacks work correctly.

---

## 3. Register OAuth redirect URIs

Once `BASE_URL` is set:

**Canva** — `canva.com/developers` → Cert-Automate → Authentication →
Authorized redirects:

```
https://YOUR-CLOUD-RUN-URL/oauth/canva/callback
```

(Keep `http://127.0.0.1:8000/oauth/canva/callback` as a second entry
for local dev.)

**Google** — done per-festival when the festival admin sets up their own
OAuth client in their own Google Cloud project. Each festival registers:

```
https://YOUR-CLOUD-RUN-URL/oauth/gmail/callback
```

---

## 4. First-time setup in the UI

1. Open `https://YOUR-CLOUD-RUN-URL/login`
2. Sign in as `INITIAL_ADMIN_EMAIL` / `INITIAL_ADMIN_PASSWORD`
3. **Admin → Festivals → New** — fill in festival name + Gmail OAuth
   client (from the festival's Google Cloud project)
4. **Admin → Users → New** — create a festival user, bind them to the
   festival
5. Sign out, sign back in as the festival user
6. **Manage → Connections → Connect Gmail** + **Connect Canva**
7. **Manage → Templates → New** for each judging status you want to
   issue certificates for (Award Winner, Finalist, …)
8. **Send certificates** — upload the FilmFreeway CSV, type the season,
   hit Send

The dashboard opens and streams progress live.

---

## 5. Auto-deploy on push to main (optional but recommended)

After the first manual deploy works:

1. Cloud Console → Cloud Build → Triggers → **Create trigger**
2. Source = your GitHub repo, branch = `^main$`
3. Configuration = Cloud Build config file → `/infra/cloudbuild.yaml`
4. Substitutions: override `_BASE_URL` to your Cloud Run URL
5. Save

From now on, every push to `main` rebuilds and redeploys automatically.

---

## 6. Known limitations to lift later

- **`max-instances=1`**. Laurel uploads land on the container's local
  disk, so a second instance wouldn't see them. Capping to 1 keeps
  this consistent. Migrate to GCS in iteration 2 and bump
  `max-instances` accordingly.
- **CSV bytes live in Mongo**. We drop them when the run finishes, but
  for very large CSVs (10k+ rows) this is suboptimal. GCS migration
  fixes this too.
- **No email tracking**. Open/click counts aren't wired up. Easy
  follow-up if it matters.
- **No password reset flow**. Admin can delete + recreate the user as
  a workaround until we add it.

None of these block running real festivals — they're future polish.

---

## 7. Quick rollback

If a deploy goes bad:

```bash
# Find the previous revision
gcloud run revisions list --service=cert-automation --region=$REGION

# Roll traffic back to it
gcloud run services update-traffic cert-automation \
  --region=$REGION \
  --to-revisions=cert-automation-00042-abc=100
```

Mongo writes from the bad revision stay — there's no schema-shape
incompatibility introduced by any of the merged PRs, so older code reads
newer docs fine.
