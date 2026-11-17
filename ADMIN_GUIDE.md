# Administrator guide

## Accounts and user workflow

Signup and login are built into the application; there is no separate administrator dashboard or invitation system. A recruiter or reviewer can open the published frontend, create an account with a name, email, and password, upload a PDF, DOCX, or TXT file, select the document, and ask questions in a new chat. The user can revisit conversations in Chat History and export the selected conversation as TXT, Markdown, or PDF. Each account's document and conversation routes are scoped to the authenticated user.

Before sharing a public demo, configure the actual Gemini key, Turso database, private B2 bucket, and frontend origin. Consider whether unrestricted public signup is appropriate for the demo. The project currently does not document an admin role, email verification, or an approval queue; do not advertise those controls.

## Production service configuration

Use `DATABASE_PROVIDER=turso`, `DATABASE_URL`, and `DATABASE_AUTH_TOKEN`; use `STORAGE_PROVIDER=r2` with the private B2 endpoint/key/bucket variables. Set `GOOGLE_API_KEY` for Gemini and `FRONTEND_URL` to the exact frontend origin. Set them with the hosting provider's backend secret manager. The frontend only needs the public `VITE_API_BASE_URL`.

Local SQLite, ChromaDB files, and local uploads are not durable production storage. Render free web services use ephemeral filesystems and may sleep/restart; use Turso and B2 for all production state. Keep separate development and production provider credentials/buckets where possible.

## Backups and recovery

- Back up Turso using a supported database export/backup procedure before schema or data maintenance. Protect exports and test restores.
- Enable appropriate object versioning/retention or scheduled backups for B2 if recovery requirements need it. Restrict application keys to a single bucket and the needed operations.
- Store backups encrypted, separate from the application repository and deployment image. Do not include `.env` or credentials unless an approved secret backup process requires them.
- Test restores in a separate test database/bucket. Never use production cleanup as a test.
- For local development only, back up `backend/database/` and `uploads/` before deleting local data. Do not delete the SQLite database file to clear test accounts; use a deliberate row-level cleanup after backup.

## Operations

- Health: `GET /api/health`.
- Backend API documentation: `/docs` on the backend host.
- Logs identify the active database provider and safe host/path information; they must not contain tokens or passwords.
- Watch database storage/read/write usage, B2 stored bytes/requests/egress, and Gemini quotas. Free quotas and terms change; check provider dashboards.
- The backend binds to `0.0.0.0:$PORT`; the Docker image defaults to 8080. Compose overrides its local backend port to 8000.

## Maintenance commands

From the repository root:

```powershell
docker compose ps
docker compose logs -f backend
docker compose logs -f frontend
docker compose down
```

For backend checks:

```powershell
Set-Location backend
.\venv\Scripts\python.exe -m unittest discover -s tests -v
.\venv\Scripts\python.exe -m compileall -q app
```

For frontend checks:

```powershell
Set-Location frontend
npm run build
```

Keep `.env`, database files, uploads, ChromaDB state, and encrypted backups out of Git. See [CONFIGURATION.md](CONFIGURATION.md), [ENVIRONMENT.md](ENVIRONMENT.md), and [DEPLOYMENT.md](DEPLOYMENT.md) for current provider and deployment settings.
