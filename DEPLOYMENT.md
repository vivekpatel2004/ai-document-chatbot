# Deployment guide

## Architecture and compatibility

Deploy the React/Vite/TypeScript frontend as static files and the FastAPI backend as a separate service. The frontend calls the API from the user's browser, so `VITE_API_BASE_URL` must be the public HTTPS API URL; a Docker-internal service name will not work in the browser. Add that frontend origin to backend `FRONTEND_URL`.

For persistent hosted data, configure Turso (`DATABASE_PROVIDER=turso`) and private S3-compatible storage (`STORAGE_PROVIDER=r2`, using the configured Backblaze B2 or Cloudflare R2 endpoint). SQLite and local storage remain supported for development. The backend checks ownership before document, chat, download, and export operations.

The backend Docker image listens on `0.0.0.0:$PORT` (default 8080). Render supplies `PORT`; Cloud Run also supplies a container port. The frontend Nginx image templates its listener from `PORT` (default 8080). Docker Compose overrides these defaults for local development.

## Vercel frontend

1. Import the repository as a Vercel project.
2. Set **Root Directory** to `frontend`.
3. Use `npm run build` and output directory `dist` (Vite).
4. Add `VITE_API_BASE_URL=https://<your-backend-host>` in the Vercel project's environment settings and redeploy.
5. Add the exact Vercel production origin to backend `FRONTEND_URL`. Add preview origins only if previews need API access.

Vite variables prefixed with `VITE_` are bundled into public client assets. Do not put Gemini, Turso, or B2 credentials in Vercel frontend variables.

## Render backend

Create a Web Service from the repository, select **Docker**, and set the Dockerfile path to `backend/Dockerfile` (or root directory `backend` with Dockerfile `Dockerfile`). Add backend variables in Render's service environment settings:

- `GOOGLE_API_KEY` (or `GEMINI_API_KEY`)
- `DATABASE_PROVIDER=turso`, `DATABASE_URL`, `DATABASE_AUTH_TOKEN`
- `STORAGE_PROVIDER=r2`, `R2_ENDPOINT`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET_NAME`
- `FRONTEND_URL=https://<your-frontend-host>`
- Optional: `LLM_PROVIDER`, `GEMINI_MODEL`, `OPENAI_API_KEY`, `OPENAI_MODEL`, `LOG_LEVEL`

The image honors Render's `PORT` and exposes `/api/health`. Do not store production documents in the container filesystem. Render's free web services have ephemeral filesystems and spin down after 15 minutes without inbound traffic; wake-up can take about a minute. Render describes free instances as suitable for testing/hobby use, not production. Its free web service may restart, and local writes are lost on restart, deploy, or sleep. Render also reserves the right to suspend free services that initiate unusually high external traffic, which matters for a backend calling Gemini, Turso, and B2. [Render free service limits](https://render.com/docs/free), [Render port binding](https://render.com/docs/web-services).

## Cloud Run backend

Build/deploy `backend/Dockerfile` as the service image. Configure the service to expose its provided `PORT` (default 8080), set the same backend environment variables as above through Cloud Run's secret/environment configuration, and allow the Vercel frontend origin in `FRONTEND_URL`. Use Turso and B2; Cloud Run instances can be replaced and local filesystem changes are not durable. The backend health endpoint is `/api/health`.

## Docker Compose development

Create `backend/.env` from `backend/.env.example`, set `GOOGLE_API_KEY`, and run at the repository root:

```powershell
docker compose up --build
```

The backend receives configuration and secrets from `backend/.env`, maps host port 8000 to its local container port, and mounts `./uploads` and `./backend/database` for development. The supplied example selects SQLite/local files; the actual `backend/.env` selectors are honored, so explicitly use `DATABASE_PROVIDER=sqlite` and `STORAGE_PROVIDER=local` for offline/local-only data. The frontend is on port 80 and uses `http://localhost:8000` by default. Set `VITE_API_BASE_URL` at build time if the browser reaches the API at another host. Stop with `docker compose down`.

Compose bind mounts are for local development. Do not carry local database or uploads mounts into production. To run Compose against Turso/B2, set the corresponding values in backend `.env`; never commit that file.

## Free usage and cost notes

Plan quotas change; check each provider before launch. At the time of this documentation update:

- Render free web services sleep after 15 idle minutes, may be restarted, have ephemeral filesystems, and have monthly instance-hour/bandwidth/build limits. They are not recommended by Render for production. A demo may therefore have cold-start delay.
- Vercel Hobby supports static Vite deployment but has project, deployment, build, and asset limits. Keep the frontend static; set secrets only on the backend. [Vercel limits](https://vercel.com/docs/limits).
- Turso currently lists a free plan with 5 GB storage, 500 million rows read/month, and 10 million rows written/month; overage behavior depends on the account's plan/settings. [Turso pricing](https://turso.tech/pricing).
- Backblaze B2 currently lists the first 10 GB as free and free egress up to 3x average stored data; additional usage can be billed. Review current storage and API request terms. [B2 pricing](https://www.backblaze.com/cloud-storage/pricing).

Using a free tier does not make Gemini API calls or all object-storage usage unlimited. Monitor each provider's usage dashboard and configure billing/spend controls if available.

## Pre-deployment checklist

- `DATABASE_PROVIDER=turso`; `DATABASE_URL` and `DATABASE_AUTH_TOKEN` are configured as backend-only secrets.
- `STORAGE_PROVIDER=r2`; B2 endpoint, bucket and application key are configured as backend-only secrets.
- `GOOGLE_API_KEY` is set; the selected Gemini model is available to that key.
- `FRONTEND_URL` exactly matches the deployed frontend origin.
- `VITE_API_BASE_URL` is the public backend URL and contains no secret.
- Signup, document upload/download/delete, document-scoped chat, citations, history, exports, and user isolation are verified against deployment-like services.
- Backup and recovery procedures are defined for Turso and B2.
- No `.env`, SQLite databases, local uploads, vectors, or temporary backup files are in the repository or container image.
