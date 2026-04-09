# PhotoStudio Core (SERVER) — v0.1

## Backend
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000

- http://127.0.0.1:8000/api/health
- http://127.0.0.1:8000/docs

Quick checks:
- curl http://127.0.0.1:8000/engine/status
- open http://127.0.0.1:8000/docs
- backend ASR import check:
  - `backend/.venv/bin/python -c "from app.engine.audio_transcript_aligner import faster_whisper_backend_self_check as c; print(c())"`

## Frontend
cd frontend
npm install
npm run dev

- http://localhost:5173/

## Splash
Put video: frontend/public/splash/splash.mp4

## Temporary public tunnel for KIE / Kling (FastAPI backend)

When `/api/clip/video` sends `image_urls` to KIE/Kling, URLs like
`http://127.0.0.1:8000/static/assets/...` are not reachable from outside,
so backend returns `KIE_LOCALHOST_IMAGE_URL_UNSUPPORTED`.

Use a temporary public tunnel and set it as `PUBLIC_BASE_URL`.

### 1) Start backend

```bash
cd backend
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

### 2) Start tunnel (recommended: cloudflared)

```bash
cloudflared tunnel --url http://127.0.0.1:8000
```

It will print a public URL like:

```text
https://random-name.trycloudflare.com
```

Alternative tools (if cloudflared is unavailable): `ngrok` or `localtunnel`.

### 3) Verify tunnel is working

Open:

- `https://random-name.trycloudflare.com/docs`
- `https://random-name.trycloudflare.com/static/assets/<your-file>.png`

If both resolve, external providers can reach backend/static files.

### 4) Configure `PUBLIC_BASE_URL`

Set in `backend/.env`:

```dotenv
PUBLIC_BASE_URL=https://random-name.trycloudflare.com
```

Then restart backend.

### 5) Validate normalization in logs

Call `/api/clip/video` and confirm logs contain:

```text
[CLIP VIDEO] public_base_url=https://random-name.trycloudflare.com
[CLIP VIDEO] normalized_source_image_url=https://random-name.trycloudflare.com/static/assets/....
```

This confirms localhost URLs are replaced by the public tunnel URL before
sending payload to KIE/Kling.

### Important constraints

`PUBLIC_BASE_URL` must **not** point to localhost-style addresses:

- `http://127.0.0.1`
- `http://localhost`
- `http://0.0.0.0`

Otherwise backend will block request with
`KIE_LOCALHOST_IMAGE_URL_UNSUPPORTED`.
