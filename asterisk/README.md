# Asterisk integration notes

1. Install Asterisk with res_pjsip, res_ari, res_ari_channels, res_ari_bridges, res_http_websocket and required codecs.
2. Merge http.conf.example and ari.conf.example into /etc/asterisk/ and replace the example password.
3. Restrict TCP 8088 to the DCS app host. Never expose ARI publicly.
4. In DCS Telephony & SIP, set ARI URL, username and Stasis app `dcs-dialler`.
5. Set ASTERISK_ARI_PASSWORD and DCS_ENCRYPTION_KEY as deployment secrets. Generate a stable Fernet key using `Fernet.generate_key()`.
6. Create SIP trunks and agent extensions in DCS, download pjsip_dcs_generated.conf, review with carrier/PBX engineer and merge into PJSIP configuration. Secrets are intentionally excluded.
7. A Stasis event worker must handle StasisStart/StasisEnd, answer agent legs, originate customer leg only after agent answer, bridge answered channels and reconcile hangups. DCS provides explicit customer-leg, bridge and hangup controls for controlled testing.
8. Keep DCS_MODE=simulation until registration, two-way audio, caller ID, recordings, event reconciliation, DNC/consent controls and legal requirements are verified. Only then set DCS_MODE=live.

The DCS web service is a call-control application, not an Asterisk PBX. Run Asterisk on a dedicated PBX host with appropriate firewall, NAT and RTP configuration.
