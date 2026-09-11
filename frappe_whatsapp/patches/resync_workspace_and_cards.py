import frappe


def execute():
	"""Force-resync the app-managed Workspace and Number Cards.

	Frappe skips re-importing Workspaces / Number Cards when the file's
	``modified`` timestamp is not newer than the copy already in the database
	(to preserve user edits). That meant colour, layout, label and card changes
	shipped by the app never reached existing sites. Forcing the reload applies
	them regardless of timestamps.
	"""
	frappe.reload_doc("frappe_whatsapp", "workspace", "whatsapp", force=True)

	for card in (
		"sent_messages",
		"delivered_messages",
		"read_messages",
		"pending_messages",
		"failed_messages",
		"received_messages",
	):
		try:
			frappe.reload_doc("frappe_whatsapp", "number_card", card, force=True)
		except Exception:
			frappe.log_error(frappe.get_traceback(), f"Resync number card failed: {card}")

	frappe.clear_cache()
