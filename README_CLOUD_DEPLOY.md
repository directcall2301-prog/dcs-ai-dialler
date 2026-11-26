# DCS AI Dialler — Render Cloud Test Deployment

This package is configured for a **private, password-protected simulation demo** on Render. It does not place telephone calls.

## Before you begin
- Create/sign in to GitHub and Render.
- Use a private GitHub repository owned by Direct Call Solutions.
- Do not upload real customer data or production lead lists to this demo.

## Publish to GitHub
1. Create a new **private** repository named `dcs-ai-dialler`.
2. Upload the contents of this folder to the repository root (the `render.yaml`, `app/`, `static/`, `requirements.txt`, etc. must be at the top level).
3. Commit the files.

## Deploy on Render
1. In Render, choose **New + → Blueprint**.
2. Connect the private GitHub repository.
3. Render detects `render.yaml` and prepares the `dcs-ai-dialler-demo` web service.
4. When prompted, set `DCS_ADMIN_USER` to a username you choose and `DCS_ADMIN_PASSWORD` to a unique, strong password (at least 16 characters). Do not reuse your email or banking password.
5. Review the service and confirm deployment. The demo is configured on Render's Free plan.
6. When deployment finishes, open the `onrender.com` URL. Your browser should prompt for the username and password.

## Important free-plan limitations
- This is a demonstration environment only.
- The SQLite database is stored at `/tmp/dcs_dialler.db`; Render's free service has an ephemeral filesystem. Test records can be lost when the service restarts, sleeps, or redeploys.
- Free web services can spin down after inactivity, so the first page load may take a little while.
- Do not enter real customer personal information.
- HTTP Basic Auth protects this demo login, but it is not the final role-based authentication system.
- There is no SIP connection, no live calling, no predictive dialling and no AI call processing in this version. The dialler remains simulation-only.

## Health check
Render checks `/health`. This endpoint returns service status only and does not expose lead or agent records.

## Before any production use
Move data to managed PostgreSQL or another persistent datastore; implement individual admin/supervisor/agent accounts, password reset, secure session management, audit review, backups, monitoring, data retention and jurisdiction-specific compliance controls. Perform a security review before entering customer data.
