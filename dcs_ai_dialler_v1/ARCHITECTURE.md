# DCS AI Dialler — Architecture and roadmap

## Product principles
1. DCS owns its application code, UX, workflows, scoring logic, campaign definitions and data model.
2. Telephony is an adapter: Asterisk ARI or FreeSWITCH can be swapped without rewriting campaign logic.
3. Compliance controls are hard gates, not AI suggestions.
4. Start with agent-assisted outbound calling; introduce predictive pacing only after enough measured traffic exists.
5. No automated marketing/AI voice campaign goes live until consent requirements and applicable UK rules have been reviewed.

## Services
- Identity & access: admin, supervisor, agent roles; MFA for privileged users.
- Campaign service: campaign scripts, operating windows, retry policies, qualification schema.
- Lead service: imports, validation, deduplication, consent evidence, source provenance.
- Compliance service: TPS/CTPS screening records, internal suppression, consent scope, local-time windows, opt-out processing, audit trail.
- Dialler service: preview, queue, pacing controller, attempt limits, abandoned-call guardrails.
- Telephony adapter: originate, answer, bridge, hangup, caller ID, call events, recording metadata.
- Agent desktop: state, customer card, script, disposition, callback scheduling.
- AI services: transcription, summaries, QA flags and lead scoring; human review for consequential decisions.
- Reporting: contact rate, right-party contact, conversion, agent occupancy, abandonment, complaints and opt-outs.

## Predictive pacing
For the first release, use preview/progressive mode. Predictive pacing is a later feature. The pacing controller should estimate agent availability and answer probability, apply a conservative dial ratio, and continuously reduce/stop dialling when live agents are unavailable or abandonment risk rises. Never use a fixed aggressive ratio as a default. Keep a strict abandoned-call ceiling below applicable regulatory thresholds and alert well before it.

## Roadmap
- V1.0 foundation: campaign registry, agent roster, lead capture, qualification, callback queue, audit events, simulation-only dialler preview. (Starter included.)
- V1.1 agent desktop: secure login/RBAC, live agent state, lead card, dispositions, callback worklist.
- V1.2 telephony lab: provision SIP trunk and Asterisk test instance; inbound/outbound test calls with synthetic numbers; recording and event webhooks.
- V1.3 controlled live pilot: compliance approval, number screening integration, calling windows, suppression, caller ID, attempt caps, monitoring and rollback.
- V1.4 progressive dialler: one call per available agent, answer detection and safe agent bridging.
- V1.5 predictive engine: measured answer-rate and agent-availability models, conservative pacing, abandonment monitoring.
- V1.6 AI layer: call transcription, summaries, QA and explainable lead prioritisation.
- V2: multi-client tenancy, white-label packaging, billing and scale beyond 10 agents.

## Cloud deployment target
Use a UK-region cloud deployment for UK lead processing where feasible, with a separate telephony region/provider selected for call quality and data-transfer requirements. Deploy API and workers in private networks; managed PostgreSQL with encryption/backups; Redis queue; secrets manager; central logs/metrics; WAF/rate limiting; TLS; least-privilege IAM; tested restore process. Confirm international data-transfer safeguards if South African staff access UK customer records.

## Open technical decisions before live calls
- Existing or preferred SIP carrier / UK number provider
- Whether Asterisk or FreeSWITCH is preferred (recommend Asterisk ARI for the initial pilot)
- Agent endpoint type: browser WebRTC, desk phones, or softphones
- Recording policy, retention period and storage region
- Exact campaign data fields and client/brand disclosures
- Compliance owner and approved lawful basis/consent wording per campaign
