# Frappe WhatsApp 1.1.0 — Routing, Reliability & Security Update

This build unifies Meta Cloud API and Evolution/Mubtkir API sending, hardens
webhooks, moves bulk campaigns to background workers, and activates maintenance
settings that previously had no effect.

## 1. Central Meta / Evolution routing

- Added **WhatsApp Settings → Default Channel** (`Meta` / `Evolution`).
- Added **Default Evolution Instance** and activated **Default Server**.
- All standard outbound paths now use the configured default channel:
  - WhatsApp Message
  - ERPNext form **Send To Whatsapp** action
  - WhatsApp Notification
  - Bulk WhatsApp Message
  - WhatsApp Scheduled Report
- An individual `WhatsApp Message` can still override the channel.
- Added one shared phone-number normalizer and one shared Evolution instance resolver.
- Existing installations with Evolution instances and no usable Meta token are
  automatically migrated to `Evolution` as the default channel.

> Interactive button/list helper APIs remain Evolution-specific because their
> payload format is Evolution-specific.

## 2. Manual / sales sending fixes

- Fixed sales/document sends silently falling back to Meta and requesting a
  missing `WhatsApp Settings.token`.
- The form action now reports the actual channel used.
- A document comment is created only after a successful send, not before it.
- Meta configuration errors now show a clear message instead of the generic
  `Password not found` error.

## 3. Incoming messages and replies

- Incoming Evolution messages are stored with:
  - `channel = Evolution`
  - the source `send_from_instance`
  - `status = Received`
- Reply keeps the incoming message's channel and Evolution instance.
- Read receipts are Meta-only and no longer write an invalid message status.
- Added a hidden `read_receipt_sent` flag to prevent duplicate read receipts.

## 4. Message status consistency

- Normalized Meta/Evolution statuses to the DocType options:
  `Pending`, `Sent`, `Delivered`, `Read`, `Failed`, `Received`.
- Late status events cannot downgrade `Read`/`Delivered` to an older state.
- Added `failure_reason` to outbound message logs.

## 5. Evolution webhook hardening

- Evolution webhook accepts POST events only (GET health response retained).
- Added optional **Evolution Webhook Secret** in WhatsApp Settings.
- Secret can be supplied as:
  - `X-Webhook-Secret`
  - `X-Evolution-Webhook-Secret`
  - `Authorization: Bearer ...`
  - `?secret=...`
- Uses constant-time secret comparison.
- Even when no secret is configured, the webhook rejects payloads that do not
  identify a locally configured `Whatsapp Instance`.
- Unknown-instance payloads are not written to the audit log.
- Query secret is removed from the stored webhook audit payload.
- Added duplicate message protection and `fromMe` filtering.
- Evolution delivery/read updates now update existing message logs correctly.

## 6. Bulk campaigns

- Bulk sending no longer sleeps inside the web request.
- Campaigns run on the Frappe **long** background worker.
- Future `scheduled_time` campaigns remain `Queued` until due.
- A scheduler checks due campaigns every 5 minutes.
- Standard bulk campaigns now respect **WhatsApp Settings → Default Channel**.
- Evolution campaigns still support per-campaign sender instance selection.
- Added durable states: `Completed`, `Partially Failed`, `Failed`, etc.
- Failed recipients store a real `WhatsApp Message` failure log and reason.
- **Retry Failed** now actually resends failed records in the background.
- Retry uses the current configured default channel, so a routing/configuration
  fix can be applied before retrying.
- `sent_count` now represents successful sends rather than attempts.
- Attachment-only campaigns are accepted.

## 7. Notifications and scheduled reports

- Notification sending is routed through the central `WhatsApp Message` sender.
- Notifications preserve reference DocType/name and channel/instance in logs.
- Generated PDF/base64 attachments are exposed as short-lived `wa-temp-*` files.
- Scheduled reports now use the selected default channel rather than being tied
  to one provider.
- Temporary public report/notification files are cleaned automatically after 48h.

## 8. Settings that now work

- `Enabled` now globally blocks outbound sending and document-event notifications.
- `Default Server` is used when creating/configuring Evolution instances.
- `Retention Days` now deletes old WhatsApp Message and Notification Log rows
  during daily maintenance.
- `Default Evolution Instance` is used as a fallback after a user-linked instance.

## 9. Performance

- The wildcard document-event hook now reads the notification map from Redis
  cache instead of querying enabled notifications on every document event.
- Cache is invalidated when WhatsApp Notification records are inserted, updated,
  or deleted.
- WhatsApp Settings are read through Frappe cache in hot paths.
- Added useful message indexes, with fresh-install safety when Custom Fields have
  not been created yet.

## 10. Legacy credential cleanup

- `Evolution Phone Settings.global_api_key` changed from plain Data to Password.
- Upgrade patch migrates existing plaintext values into Frappe password storage.
- Removed broken legacy QR/status/logout buttons and directs users to
  **Whatsapp Instance**, where those operations are maintained.

## 11. Tests / release metadata

- Added focused tests for number normalization, status mapping/order,
  attachment media recognition and channel constants.
- Version bumped from **1.0.7** to **1.1.0**.
- Python files compile and all app JSON/JavaScript files pass static syntax checks.

## Upgrade

After deploying this build, run the normal Frappe migration/build/restart for the
site so the patch, Custom Fields and scheduler changes are applied.

Typical bench flow:

```bash
bench --site <site-name> migrate
bench build --app frappe_whatsapp
bench restart
```

Then open **WhatsApp Settings**, confirm **Default Channel**, and when Evolution
is selected confirm **Default Evolution Instance** / **Default Server**. For the
strongest webhook protection, set **Evolution Webhook Secret** and configure the
same value on the Evolution webhook sender.
