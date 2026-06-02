# Certificate Automation — Backend (Phase 1)

FastAPI server with server-rendered Jinja2 UI for managing festivals,
templates, and certificate runs. Persistence in MongoDB.

## Phase 1 scope (this PR)

- Login / logout with bcrypt + signed-cookie sessions
- Admin-only festival CRUD UI
- Bootstrap admin from env on first boot
- At-rest Fernet encryption for Gmail secrets
- Dockerfile for Cloud Run
- Health check at `/healthz`

Festival user UI, template CRUD, CSV upload + send, run dashboard, and
Cloud Tasks worker land in Phase 2 + 3.

## Local dev

```bash
# 1. Mongo
docker run -d --name cert-mongo -p 27017:27017 mongo:7

# 2. Python deps
python -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements.txt

# 3. Config
cp backend/.env.example backend/.env
# Edit backend/.env — generate SESSION_SECRET and ENCRYPTION_KEY:
python -c "import secrets; print('SESSION_SECRET=' + secrets.token_urlsafe(48))"
python -c "from cryptography.fernet import Fernet; print('ENCRYPTION_KEY=' + Fernet.generate_key().decode())"

# 4. Run
uvicorn backend.app.main:app --reload --port 8000
```

Open <http://localhost:8000/login> and sign in with the
`INITIAL_ADMIN_EMAIL` / `INITIAL_ADMIN_PASSWORD` from `.env`.
(That admin is only auto-created on first boot when the users
collection is empty.)

## Project layout

```
backend/
├── app/
│   ├── main.py            FastAPI app + lifespan + bootstrap admin
│   ├── settings.py        Pydantic env config
│   ├── auth/
│   │   └── service.py     bcrypt + session helpers + role dependencies
│   ├── db/
│   │   ├── client.py      Motor connection + collection accessors
│   │   └── models.py      Pydantic models (User, Festival, CertTemplate, Run)
│   ├── routes/
│   │   ├── auth.py        /login, /logout
│   │   └── admin.py       /admin/festivals CRUD
│   ├── services/
│   │   └── crypto.py      Fernet encrypt/decrypt
│   ├── templates/         Jinja2 HTML
│   └── static/css/        Stylesheet
└── Dockerfile             Multi-stage build for Cloud Run
```

## What's encrypted in Mongo

| Field                          | Why                                |
|--------------------------------|------------------------------------|
| `festivals.gmail.client_secret_enc` | OAuth client secret              |
| `festivals.gmail.refresh_token_enc` | Long-lived Gmail refresh token   |
| `festivals.canva.client_secret_enc` | (when per-festival Canva is used) |
| `festivals.canva.refresh_token_enc` | (same)                            |

Plaintext never persists. We hold them in-memory only during a request.

## Deploying to Cloud Run (preview)

```bash
gcloud builds submit --tag gcr.io/$PROJECT/cert-automation
gcloud run deploy cert-automation \
  --image gcr.io/$PROJECT/cert-automation \
  --region asia-south1 \
  --set-secrets MONGO_URI=mongo-uri:latest,SESSION_SECRET=session-secret:latest,ENCRYPTION_KEY=encryption-key:latest \
  --set-env-vars ENV=production,BASE_URL=https://certs.example.com \
  --allow-unauthenticated
```

Full Cloud Build YAML + IAM setup arrives with Phase 3.
