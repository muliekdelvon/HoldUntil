# Deploying HoldUntil to Coolify

This guide explains how to deploy **HoldUntil** on **Coolify** using Docker.

---

## 1. Overview of Coolify Additions

Similar to the reference deployment structure:
- **`Dockerfile`**: A multi-stage build that:
  1. Compiles the frontend (React + Vite) with Node 20.
  2. Builds the backend runtime (Python 3.12 slim + FastAPI/Uvicorn).
  3. Bundles the compiled frontend directly into the application so both the API and Web UI run from a single container and domain.
  4. Includes `curl` for container health monitoring.
  5. Implements security best practices (non-root `appuser`).
- **`docker-compose.yml`**: Production compose service definition with healthcheck, persistent storage volume, and environment variable passing.
- **`.dockerignore`**: Excludes virtual environments, caches, secrets, and local databases from the image.
- **Health Check Endpoints**: `/health` and `/api/health` return HTTP 200 for Coolify's container health monitor.
- **Automatic SPA Routing**: Serves static React assets under `/assets` and fallback to `index.html` for client-side routing while keeping `/api/*` endpoints intact.

---

## 2. Deploy via Coolify (Step-by-Step)

### Step 1: Create a New Resource in Coolify
1. In your Coolify dashboard, select your **Project** and **Environment**.
2. Click **+ Add Resource** -> **Public Repository** or **Private Repository (GitHub)**.
3. Enter your repository URL (e.g., `https://github.com/your-username/HoldUntil.git`).
4. Select the branch you want to deploy (e.g., `main`).

### Step 2: Choose Build Pack
- Coolify will detect either **Dockerfile** or **Docker Compose**.
- **Recommended**: Select **Dockerfile** (or **Docker Compose**).
- Set **Port**: `8000`.

### Step 3: Configure Persistent Storage (Crucial for SQLite)
If you are using SQLite instead of external PostgreSQL:
1. Under **Storages / Persistent Storage**, add a volume:
   - **Name**: `holduntil_data`
   - **Destination**: `/app/data`
2. This ensures your SQLite database file (`/app/data/holduntil.db`) survives container updates and rebuilds.
*(If you attach a managed PostgreSQL database in Coolify, set `DATABASE_URL=postgresql://user:pass@host:5432/holduntil` instead).*

### Step 4: Configure Health Check
- **Health Check Path**: `/health`
- **Port**: `8000`

### Step 5: Configure Environment Variables
In the Coolify **Environment Variables** tab, paste the required variables (from `.env.example`):

```bash
PORT=8000
DEBUG=False
SIMULATION_MODE=false
DATABASE_URL=sqlite:////app/data/holduntil.db

# Safaricom Daraja (M-Pesa)
DARAJA_ENV=production
DARAJA_CONSUMER_KEY=your_daraja_key
DARAJA_CONSUMER_SECRET=your_daraja_secret
DARAJA_TRANSACTION_TYPE=CustomerPayBillOnline
DARAJA_SHORTCODE=your_shortcode
DARAJA_BUY_GOODS_TILL=
DARAJA_PASSKEY=your_passkey
DARAJA_CALLBACK_URL=https://your-domain.com/api/webhook/daraja/callback
DARAJA_B2C_CALLBACK_URL=https://your-domain.com/api/webhook/daraja/b2c_callback
DARAJA_B2C_SHORTCODE=your_b2c_shortcode
DARAJA_INITIATOR_NAME=your_initiator_name
DARAJA_SECURITY_CREDENTIAL=your_security_credential

# Meta WhatsApp Cloud API
META_API_VERSION=v18.0
META_VERIFY_TOKEN=your_meta_verify_token
META_ACCESS_TOKEN=your_meta_system_user_token
META_PHONE_NUMBER_ID=your_phone_number_id
BOT_WHATSAPP_NUMBER=your_bot_phone_number

# AI Moderator (Gemini)
GEMINI_API_KEY=your_gemini_api_key
```

### Step 6: Deploy
Click **Deploy**! Coolify will:
1. Clone the repository.
2. Build the React frontend via Node 20.
3. Install Python dependencies and copy assets into Python 3.12 slim.
4. Mount the persistent volume and launch Uvicorn on port 8000.
5. Provide automatic SSL certificate via Traefik.

---

## 3. Webhook Endpoints
Once your domain is live (e.g. `https://escrow.yourdomain.com`):
- **WhatsApp Webhook**: `https://escrow.yourdomain.com/api/webhook/meta`
  - Verify Token: Matches `META_VERIFY_TOKEN`
- **Daraja STK Callback**: `https://escrow.yourdomain.com/api/webhook/daraja/callback`
- **Daraja B2C Callback**: `https://escrow.yourdomain.com/api/webhook/daraja/b2c_callback`
- **Health Check**: `https://escrow.yourdomain.com/health`
- **Web UI & Dashboard**: `https://escrow.yourdomain.com/`

