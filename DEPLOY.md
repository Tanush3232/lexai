# LexAI — Deployment Guide (AWS Lightsail)

This guide covers:
1. Pushing your code to GitHub
2. Setting up AWS Lightsail
3. Deploying the full stack with Docker Compose

---

## Part 1 — Push to GitHub (Local Machine)

Run these commands **on your Windows machine** from the project root:

```bash
# Navigate to the project
cd "C:\Users\tanush.angrish\Desktop\Tanush\LexAI"

# Initialize git if you haven't already
git init
git remote add origin https://github.com/<your-username>/LexAI.git

# Stage everything (secrets, logs, dev scripts are excluded by .gitignore)
git add .

# Review what's being committed (sanity check — .env should NOT appear)
git status

# Commit
git commit -m "feat: production-ready LexAI deployment setup"

# Push
git push -u origin main
```

> ⚠️ **Before pushing**, confirm that `.env` is NOT shown in `git status`. If it appears, run `git rm --cached .env` first.

---

## Part 2 — Lightsail Instance Setup

### 2a. SSH into your instance

Download your Lightsail SSH key from the AWS console, then:

```bash
# Windows (PowerShell) or Linux/Mac
ssh -i ~/Downloads/LexAi.pem ubuntu@<YOUR_LIGHTSAIL_PUBLIC_IP>
```

### 2b. Install Docker & Docker Compose

```bash
# Update system
sudo apt-get update && sudo apt-get upgrade -y

# Install Docker
curl -fsSL https://get.docker.com | sudo sh

# Add ubuntu user to docker group (no sudo needed for docker commands)
sudo usermod -aG docker ubuntu

# Log out and back in (apply group change)
exit
# SSH back in
ssh -i ~/Downloads/LexAi.pem ubuntu@<YOUR_LIGHTSAIL_PUBLIC_IP>

# Verify Docker works
docker --version
docker compose version
```

### 2c. Open Lightsail Firewall Ports

In the **AWS Lightsail console** → Your Instance → **Networking** tab → **Add rule**:

| Port | Protocol | Purpose |
|------|----------|---------|
| 80   | TCP      | Main app (Nginx) |
| 9000 | TCP      | MinIO file downloads (presigned URLs) |

> Port 22 (SSH) is already open by default.

---

## Part 3 — Deploy the Application

### 3a. Clone the repo

```bash
git clone https://github.com/<your-username>/LexAI.git
cd LexAI
```

### 3b. Create `.env`

```bash
# Copy the template
cp .env.example .env

# Edit it with your values
nano .env
```

Fill in these **required** values in `.env`:

```
SERVER_IP=<the public IP shown on your Lightsail dashboard>
GOOGLE_API_KEY=<your Gemini API key>
JWT_SECRET=<run: openssl rand -hex 32>
MINIO_PUBLIC_URL=http://<SERVER_IP>:9000
NEXT_PUBLIC_API_URL=http://<SERVER_IP>
CORS_ORIGINS=["http://<SERVER_IP>", "http://localhost:3000"]
```

Leave all other values as their defaults unless you want to change passwords.

### 3c. Build and start everything

```bash
# Build all images and start in background
# First run takes 10-15 minutes (downloads images, compiles Next.js)
docker compose up -d --build

# Watch the logs during startup
docker compose logs -f --tail=50
```

### 3d. Wait for services to be healthy

```bash
# Check container status
docker compose ps

# All services should show "healthy" or "running"
# Elasticsearch takes the longest (~90 seconds)
```

### 3e. Run database migrations

```bash
# Run alembic migrations (only needed on first deploy or after schema changes)
docker compose exec api alembic upgrade head
```

### 3f. Verify the app is running

Open your browser:
- **App**: `http://<YOUR_LIGHTSAIL_PUBLIC_IP>/`
- **API docs**: `http://<YOUR_LIGHTSAIL_PUBLIC_IP>/docs`

---

## Part 4 — Updating the App (Re-deploy)

When you push new code and want to update Lightsail:

```bash
# On your local machine — push changes
git add .
git commit -m "your change description"
git push origin main

# SSH into Lightsail
ssh -i ~/Downloads/LexAi.pem ubuntu@<YOUR_LIGHTSAIL_PUBLIC_IP>
cd LexAI

# Pull latest code
git pull origin main

# Rebuild only changed services and restart
docker compose up -d --build

# View logs
docker compose logs -f api web
```

---

## Part 5 — Useful Commands on Lightsail

```bash
# View all container statuses
docker compose ps

# View logs for a specific service
docker compose logs -f api
docker compose logs -f worker
docker compose logs -f web

# Restart a single service without rebuilding
docker compose restart api

# Stop everything
docker compose down

# Stop and delete all data volumes (⚠️ destroys the database!)
docker compose down -v

# Check memory usage (important on 2 GB instance)
free -h
docker stats --no-stream

# Run a command inside a container
docker compose exec api bash
docker compose exec api alembic upgrade head
```

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| App not loading | Check nginx: `docker compose logs nginx` |
| API errors | Check: `docker compose logs api` |
| Out of memory | Check: `free -h`, restart Elasticsearch: `docker compose restart elasticsearch` |
| Build fails | Check disk: `df -h`, prune: `docker system prune -f` |
| Elasticsearch red | Wait 2 minutes, then: `docker compose restart elasticsearch` |
| MinIO files not loading | Ensure port 9000 is open in Lightsail firewall |

---

## Architecture

```
Browser
  │
  └─ :80 → Nginx ─┬─ / ──────► web (Next.js :3000)
                  └─ /api/ ──► api (FastAPI :8000)

Browser
  └─ :9000 → MinIO (direct, for presigned file downloads)

Internal (Docker network only):
  api / worker → postgres, redis, qdrant, elasticsearch, neo4j, minio
```
