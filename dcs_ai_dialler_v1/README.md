# DCS AI Dialler V1.0 — Browser Simulator

Direct Call Solutions-owned starter application for testing campaign, lead and agent workflows before SIP/telephony is connected.

## What works
- Browser dashboard at `/`
- Four preloaded campaigns: UK Life Insurance, Energy, Broadband and Mobile SIMs
- Up to 10 active agents, with status management
- Manual lead creation and lead list/filtering
- Qualification notes and callback date/time capture
- Dashboard totals and campaign counts
- Safe dialler preview endpoint and browser simulator
- SQLite persistence
- API documentation at `/docs`

## What does not work yet
- No real calls are placed; no SIP trunk or phone numbers are connected
- No predictive dialling, speech AI, CSV upload, user authentication or production security yet
- The current compliance checks are conservative demo gates, not legal advice or a compliance certification

## Run locally (Windows)
1. Install Python 3.11 or newer.
2. Extract the ZIP to a folder you control.
3. Double-click `START_DCS_WINDOWS.bat`.
4. The script creates a virtual environment, installs dependencies and starts the app.
5. Open http://127.0.0.1:8000 in Chrome or Edge.
6. Press Ctrl+C in the terminal to stop the server.

## Run locally (macOS/Linux)
```bash
chmod +x start_dcs.sh
./start_dcs.sh
```
Then open http://127.0.0.1:8000.

## Docker
```bash
docker build -t dcs-ai-dialler .
docker run --rm -p 8000:8000 -v dcs-data:/app/data -e DATABASE_PATH=/app/data/dcs_dialler.db dcs-ai-dialler
```

## Safety
The UI clearly labels simulation mode. The dialler preview never originates calls. Do not use real customer data until access controls, secure hosting, privacy notices, retention, lawful-basis/consent checks, suppression screening, calling windows, call recording controls and jurisdiction-specific legal review have been implemented.


## Cloud test deployment
See `README_CLOUD_DEPLOY.md` for a password-protected Render demo deployment. The free demo uses temporary storage and must only contain dummy test data.
