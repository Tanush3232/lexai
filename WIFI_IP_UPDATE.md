# LexAI Daily IP Update Guide (Internal Instructions)

This guide is for the AI assistant to follow whenever the user's IP changes (daily WiFi/LAN change). 

When the user says "my IP has changed" or "update to 192.168.X.X", follow these exact steps:

### 1. Update `.env` (Root Directory)
- Location: `.\.env`
- Replace ALL occurrences of the old IP with the **<new-ip>**.
- **Crucial Variables:**
  - `MINIO_PUBLIC_URL=http://<new-ip>:9000` (Fixes PDF access/uploads)
  - `NETWORK_IP=<new-ip>`
  - `CORS_ORIGINS='["http://localhost:3000","http://<new-ip>:3000"]'`

### 2. Update Next.js Config
- Location: `.\apps\web\next.config.ts`
- Update `allowedDevOrigins` to include the specific IP:
  ```typescript
  allowedDevOrigins: ["<new-ip>", "localhost"]
  ```
- *Note: Next.js strictly requires the literal IP here for HMR/Hot-reloading to work over the network.*

### 3. Verify Backend CORS Regex
- Location: `.\apps\api\app\main.py`
- Ensure `allow_origin_regex` is set to `r".*"` for development.
  ```python
  app.add_middleware(
      CORSMiddleware,
      allow_origins=settings.CORS_ORIGINS,
      allow_origin_regex=r".*",
      allow_credentials=True,
      allow_methods=["*"],
      allow_headers=["*"],
  )
  ```

### 4. Restart Services (Important!)
Instruct the user to restart the following because they do **not** hot-reload `.env` or config changes:
1.  **Stop and Restart Backend:** `uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload`
2.  **Stop and Restart Frontend:** `npm run dev`

### 5. Troubleshooting "CORS" Errors
If Chrome still says "Blocked by CORS policy" or `net::ERR_FAILED`:
- **Check Status Code:** If it's a `500 Internal Server Error`, it's likely a missing dependency causing a crash (common after new feature additions).
- **Check Dependencies:** Ensure `rapidfuzz` and `pdfplumber` are installed in the backend environment.
- **Firewall:** If `net::ERR_FAILED` persists on a new network, check if Windows Firewall is blocking Ports 8000, 9000, or 3000.
