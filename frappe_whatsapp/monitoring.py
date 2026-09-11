# Copyright (c) 2026, Shridhar Patil and contributors
# For license information, please see license.txt
"""Server-side monitoring of WhatsApp instance connectivity.

A scheduled job polls each instance's live connection state and, when a
previously-connected instance drops, notifies the site's System Managers and the
operating company's supervisor by email and an internal desk notification. A
per-instance ``disconnect_alerted`` flag prevents the same drop being reported
on every run; it is cleared automatically once the instance reconnects.
"""

import frappe
from frappe import _


def check_instance_connections():
	"""Scheduled entry point: reconcile every monitored instance's live state.

	Only instances that have been connected at least once are monitored, so a
	brand-new instance that was never linked never raises a false alarm. If the
	Mubtkir API server cannot be reached for an instance, that instance is
	skipped (an outage on our side must not look like a customer disconnect).
	"""
	settings = frappe.get_cached_doc("WhatsApp Settings")
	if not settings.get("enable_disconnect_alert"):
		return

	# Import here to avoid a heavy import at module load / during migrate.
	from frappe_whatsapp.api import get_instance_status

	instances = frappe.get_all(
		"Whatsapp Instance",
		fields=[
			"name",
			"instance_name",
			"connection_status",
			"phone_number",
			"connected_since",
			"disconnect_alerted",
			"evolution_server",
			"linked_user",
		],
	)

	for inst in instances:
		# Never alert for an instance that was never actually connected.
		if not inst.evolution_server or (not inst.phone_number and not inst.connected_since):
			continue

		try:
			result = get_instance_status(inst.name)
		except Exception:
			# Server unreachable / instance missing remotely — do not false-alarm.
			continue

		status = (result or {}).get("status")

		if status == "Disconnected" and not inst.disconnect_alerted:
			try:
				_notify_disconnect(inst, settings)
			finally:
				frappe.db.set_value("Whatsapp Instance", inst.name, "disconnect_alerted", 1)
				frappe.db.commit()
		elif status == "Connected" and inst.disconnect_alerted:
			# Reconnected — reset the flag so a future drop alerts again.
			frappe.db.set_value("Whatsapp Instance", inst.name, "disconnect_alerted", 0)
			frappe.db.commit()


def _system_manager_users():
	"""Return enabled users that hold the System Manager role (email + name)."""
	names = frappe.get_all(
		"Has Role",
		filters={"role": "System Manager", "parenttype": "User"},
		pluck="parent",
	)
	names = [n for n in set(names) if n not in ("Administrator", "Guest")]
	if not names:
		return []
	return frappe.get_all(
		"User",
		filters={"name": ["in", names], "enabled": 1},
		fields=["name", "email"],
	)


def _notify_disconnect(inst, settings):
	"""Send the disconnect email + internal notifications for one instance."""
	label = inst.instance_name or inst.name
	phone = inst.phone_number or _("unknown")

	subject = _("⚠ WhatsApp disconnected: {0}").format(label)
	intro = _(
		"The WhatsApp instance <b>{0}</b> (number {1}) has lost its connection "
		"and is no longer able to send or receive messages."
	).format(frappe.utils.escape_html(label), frappe.utils.escape_html(phone))
	action = _("Open the instance in ERPNext, click <b>Show QR Code</b> and re-scan it to reconnect.")
	body = f"<p>{intro}</p><p>{action}</p>"

	managers = _system_manager_users()
	recipients = [m.email for m in managers if m.get("email")]

	supervisor_email = (settings.get("company_supervisor_email") or "").strip()
	if supervisor_email and supervisor_email not in recipients:
		recipients.append(supervisor_email)

	# --- Email alert ---
	if recipients:
		try:
			frappe.sendmail(
				recipients=recipients,
				subject=subject,
				message=body,
				reference_doctype="Whatsapp Instance",
				reference_name=inst.name,
			)
		except Exception:
			frappe.log_error(
				message=frappe.get_traceback(),
				title=f"Disconnect email failed for {inst.name}",
			)

	# --- Internal desk notification (for each System Manager) ---
	for m in managers:
		try:
			frappe.get_doc(
				{
					"doctype": "Notification Log",
					"subject": subject,
					"email_content": body,
					"for_user": m.name,
					"type": "Alert",
					"document_type": "Whatsapp Instance",
					"document_name": inst.name,
				}
			).insert(ignore_permissions=True)
		except Exception:
			frappe.log_error(
				message=frappe.get_traceback(),
				title=f"Disconnect notification failed for {inst.name}",
			)
