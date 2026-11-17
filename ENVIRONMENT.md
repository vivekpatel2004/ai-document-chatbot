# Environment setup

## Windows local development

From the repository root in PowerShell:

```powershell
Copy-Item backend/.env.example backend/.env
notepad backend/.env
```

Set `GOOGLE_API_KEY`, then install/run the backend:

```powershell
Set-Location backend
py -3.11 -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

In another PowerShell window:

```powershell
Set-Location frontend
npm ci
npm run dev
```

The Vite page is `http://localhost:5173`; the API defaults to port 8000. Use `DATABASE_PROVIDER=sqlite` and `STORAGE_PROVIDER=local` for development. Local SQLite is stored under `backend/database/` by default, and local uploads are stored under the project `uploads/` directory.

## Production backend environment

Configure these as backend service secrets, not in the frontend:

```dotenv
LLM_PROVIDER=gemini
GOOGLE_API_KEY=<secret>
GEMINI_MODEL=gemini-3.8-flash
DATABASE_PROVIDER=turso
DATABASE_URL=libsql://<your-turso-host>
DATABASE_AUTH_TOKEN=<secret>
STORAGE_PROVIDER=r2
R2_ENDPOINT=https://s3.<region>.backblazeb2.com
R2_ACCESS_KEY_ID=<secret>
R2_SECRET_ACCESS_KEY=<secret>
R2_BUCKET_NAME=<private-bucket>
FRONTEND_URL=https://<your-frontend-domain>
```

Cloudflare R2 uses the same adapter variables, with its R2 S3 endpoint and account ID where required. Do not store actual values in this document, source control, or a frontend `.env` file. Use the hosting service's encrypted secret/environment settings and restrict the B2 application key to the intended bucket and operations.

## Frontend environment

`VITE_API_BASE_URL` is compiled into browser code, so it must contain only the public API URL:

```dotenv
VITE_API_BASE_URL=https://<your-backend-domain>
```

For a Vercel Vite project, configure that variable under project environment settings and redeploy. Do not prefix backend credentials with `VITE_`.

## Docker Compose

Compose reads backend variables from `backend/.env`. The supplied example starts local SQLite/local storage with the development ports and bind mounts. It is safe to edit `backend/.env`; the Dockerfiles and ignore files exclude secrets and local data from the images. See [DEPLOYMENT.md](DEPLOYMENT.md).

## Provider selection

The application reads `DATABASE_PROVIDER`/`DATABASE_URL` and `STORAGE_PROVIDER` on the backend. Use `sqlite` and `local` while developing; select `turso` and `r2` in the deployed backend environment. Selecting hosted providers does not copy existing SQLite rows or local files automatically. Back up and migrate deliberately before changing a live deployment.
