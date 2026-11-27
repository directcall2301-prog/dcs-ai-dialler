# DCS AI Dialler V3.0 — Integrated Contact Centre Platform

Owned product identity: Direct Call Solutions Pty Ltd.

## Included in this integrated release
- Secure administrator login and agent accounts (PBKDF2 password hashing).
- Dynamic campaign creation, editing, pausing and deletion.
- Campaign-specific predictive/progressive/preview strategy settings, max attempts, pacing ratio, abandon ceiling and caller ID field.
- Lead creation, editing, CSV import/export, filtering and campaign assignment.
- Consent state and Do Not Call suppression controls.
- Agent roster and agent states (offline, ready, paused, on-call, wrap).
- Predictive queue selection: ready-agent gate, callback prioritisation, attempt ceiling and DNC exclusion.
- Call disposition logging and callback scheduling.
- Qualification answer capture API.
- Operational dashboard, campaign/agent/disposition reports and admin audit trail.
- Render deployment configuration and persistent-disk database path.
- Safe simulation mode by default.

## Important live-telephony boundary
This is a complete integrated application foundation and a testable predictive queue/control system. It does **not** place live PSTN calls in this release. A production predictive dialler requires a configured telephony control plane (normally Asterisk), SIP trunk/carrier credentials, registered agent endpoints, ARI/Stasis call orchestration, answer-machine handling, bridge/transfer logic, call recording storage, event reconciliation, and verified regulatory controls. These cannot be responsibly activated or tested without DCS's carrier account, server and approved call-flow details. Do not set `DCS_MODE=live` expecting live calls; the live execution endpoint is deliberately blocked.

## Deploy on Render
1. Make the GitHub repository private.
2. Upload the contents of this folder to the repository root (not inside another nested folder).
3. Create a Render Web Service from that repository, or use the included `render.yaml`.
4. Set `DCS_ADMIN_USERNAME` and `DCS_ADMIN_PASSWORD` as Render environment variables. Use a unique password of at least 16 characters.
5. Keep `DCS_MODE=simulation`.
6. Deploy. Open `/health`, then sign in at `/`.
7. Add campaigns, agents, then import a CSV lead list.

## Local Windows test
Install Python 3.12+, open Command Prompt in this folder:
```bat
py -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
set DCS_MODE=simulation
set DCS_ADMIN_USERNAME=admin
set DCS_ADMIN_PASSWORD=Use-A-Unique-Long-Password
set DCS_SECRET_KEY=replace-with-a-long-random-secret
uvicorn app.main:app --reload
```
Open http://127.0.0.1:8000.

## CSV format
Required: `phone`. Recommended columns:
`first_name,last_name,phone,email,consent_status,timezone`
Consent values: `CONSENTED`, `UNVERIFIED`, `NO_CONSENT`, `DO_NOT_CALL`.
Only properly sourced and appropriately permissioned contacts should be loaded. `UNVERIFIED` records are retained for review but the queue should be restricted to records with verified lawful basis before production dialing.

## Production-readiness checklist
- [ ] Select and contract a SIP carrier / UK termination provider.
- [ ] Provision Asterisk and secure ARI/Stasis application.
- [ ] Create agent PJSIP endpoints and secure credentials.
- [ ] Implement originate → answer detection → agent bridge → transfer / hangup events.
- [ ] Implement answering-machine detection and failed-call retry logic.
- [ ] Implement live pacing based on actual answer rate, agent occupancy, abandon rate and regulatory ceiling.
- [ ] Configure call recording, encryption, retention and access controls.
- [ ] Configure consent evidence, UK TPS/CTPS and internal suppression screening, caller ID rules, local calling windows, privacy notice and recording disclosures.
- [ ] Conduct load, failover, security and abandonment-rate tests before production.

## Security notes
Use HTTPS, private GitHub repository, strong secrets, persistent disk or managed PostgreSQL, backups and restricted administrator access. Rotate the initial credentials after deployment. This release uses SQLite for simple deployment; for multi-instance/high-volume operation, migrate to PostgreSQL and a background job/event architecture.

## V4 telephony control plane

V4 adds provider-independent SIP trunk profiles, agent SIP extension provisioning, Asterisk ARI connectivity checks, PJSIP config generation, ARI channel origination, customer-leg origination, mixing-bridge creation and hangup controls. See `asterisk/README.md` for PBX installation and validation.

**Important:** this is an integration-ready control plane, not a hosted telephone network. Asterisk, a carrier SIP trunk, RTP/network configuration, and a Stasis event worker are still required for full production call handling. The browser never receives carrier or ARI passwords. Keep simulation mode until the complete audio and compliance path has been tested.

### Automatic ARI event flow

When `DCS_MODE=live` and ARI credentials are supplied, the background ARI WebSocket worker subscribes to Stasis events. It answers the agent leg first, originates the customer through the first active trunk only after the agent leg enters Stasis, answers the customer leg, creates a mixing bridge, and cleans up the opposite channel/bridge on channel destruction. This flow still requires real PBX/carrier validation; do not treat a successful API response as proof of two-way audio.
