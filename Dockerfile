# Matcher: one image = the web app + the API (FastAPI serves both), so there is ONE thing to deploy.
#   docker build -t matcher .
#   docker run -p 8000:8000 --env-file backend/.env matcher        -> http://localhost:8000
# Settings (keys, MongoDB, Google client, ALLOWED_EMAILS) come from environment variables;
# nothing secret is baked into the image.

# ---------- 1) build the web app (frontend/dist) ----------
FROM node:20-bookworm-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---------- 2) the server ----------
FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=8000

# LibreOffice Writer (run headless) turns the Word resume into a PDF to measure the one-page fit;
# Carlito has exactly Calibri's letter widths, so lines wrap like they do in Word.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libreoffice-writer fonts-crosextra-carlito fonts-dejavu-core \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app/backend
COPY backend/requirements.txt ./
RUN pip install -r requirements.txt

COPY backend/ ./
COPY --from=web /web/dist /app/frontend/dist

# run as a normal user, not root
RUN useradd --create-home matcher && chown -R matcher /app
USER matcher

EXPOSE 8000
# --proxy-headers: behind Render's HTTPS proxy, so sign-in cookies are marked secure
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'"]
