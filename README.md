# AI Document Chatbot

A chat-first document assistant built with React, Vite, TypeScript, FastAPI, SQLite/libSQL, Gemini, and Sentence Transformers. Users upload private PDF, DOCX, or TXT files, then ask questions grounded in selected documents. Answers stream into a persistent conversation with page-aware citations.

## Features

- Email/password signup and login with server-validated ownership for documents, conversations, messages, downloads, and exports.
- New Chat, Overview, Documents, Chat History, and Settings navigation.
- Private PDF, DOCX, and TXT uploads. Local disk is the development default; Backblaze B2 or Cloudflare R2 use the existing S3-compatible adapter.
- Sentence Transformers embeddings stored with document chunks in the database; RAG retrieval scopes context to selected, authenticated-user documents.
- Gemini streaming, citations with filename/page references, persistent chat history, light/dark themes, and TXT/Markdown/PDF export.
- SQLite for local development and Turso/libSQL for hosted persistence.

## Local setup on Windows

Install Python 3.11+, Node.js 20+, and npm. From PowerShell at the project root:

```powershell
Copy-Item backend/.env.example backend/.env
Set-Location backend
py -3.11 -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Set-Location ..\frontend
npm ci
```

Edit `backend/.env` and set `GOOGLE_API_KEY`. Keep SQLite and local file storage for a simple local run. Start the services in separate terminals:

```powershell
# Terminal 1, from backend/
.\venv\Scripts\Activate.ps1
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000

# Terminal 2, from frontend/
npm run dev
```

Open <http://localhost:5173>. Sign up, upload a PDF/DOCX/TXT document, select it for a new chat, ask a question, then revisit the conversation from Chat History. Use the conversation export control to download TXT, Markdown, or PDF.

## Environment and services

Copy the backend template to `backend/.env`. The full variable reference and provider examples are in [CONFIGURATION.md](CONFIGURATION.md) and [ENVIRONMENT.md](ENVIRONMENT.md).

- Local mode: `DATABASE_PROVIDER=sqlite`, `STORAGE_PROVIDER=local`.
- Hosted mode: set `DATABASE_PROVIDER=turso`, `DATABASE_URL`, and `DATABASE_AUTH_TOKEN`; set `STORAGE_PROVIDER=r2` with the B2 or R2 endpoint, access key, secret key, and bucket name. The `R2_*` names are retained for compatibility with the S3 adapter.
- Keep every database, Gemini, and object-storage secret on the backend. Only the public API URL belongs in `VITE_API_BASE_URL`.
- Do not use local SQLite, `uploads/`, or ChromaDB directories as production persistence. The app's current RAG data is stored in database chunks; old local Chroma files are not required by the current request path.

## Docker development

Create `backend/.env` and set `GOOGLE_API_KEY`, then run from the project root:

```powershell
docker compose up --build
```

The frontend is at <http://localhost>, the API at <http://localhost:8000>, and the health endpoint is `/api/health`. Compose passes the backend configuration from `backend/.env`; the example file defaults to SQLite and local uploads. Set those providers to `sqlite`/`local` for local-only development, or to `turso`/`r2` to deliberately use hosted services. Do not rely on container files for production persistence.

## Deployment

The frontend can be deployed as a Vite static site on Vercel; set its root directory to `frontend`, build command to `npm run build`, output directory to `dist`, and set `VITE_API_BASE_URL` to the public backend URL. Deploy the FastAPI service separately, such as a Docker web service on Render or Cloud Run. Set Turso and B2/Gemini secrets in the backend platform's secret/environment settings and set `FRONTEND_URL` to the deployed frontend origin.

Free plans are suitable for a demo but have quotas and service-specific limits. Render free web services sleep after inactivity and have ephemeral filesystems, so use Turso and B2 for persistence. Vercel Hobby has deployment/build limits. Turso and B2 include free usage subject to published quotas and billing terms. Check current plan terms before launch; see [DEPLOYMENT.md](DEPLOYMENT.md).

## Checks

```powershell
Set-Location backend
python -m unittest discover -s tests -v
python -m compileall -q app
Set-Location ..\frontend
npm run build
```

For operations and backup guidance, see [ADMIN_GUIDE.md](ADMIN_GUIDE.md).
