"""Reload puller doctypes so their new fields reach existing sites.

New fields were added to WhatsApp Contact Puller (quality_score, tier_summary,
winback_preset), WhatsApp Pulled Number (tier, best_send_hour, read_no_reply)
and WhatsApp Recipient (recipient_code). reload_doc imports the updated JSON.
"""

import frappe


def execute():
	for dt in ("whatsapp_contact_puller", "whatsapp_pulled_number", "whatsapp_recipient"):
		try:
			frappe.reload_doc("frappe_whatsapp", "doctype", dt, force=True)
		except Exception:
			frappe.log_error(frappe.get_traceback(), f"Reload doctype failed: {dt}")
	frappe.clear_cache()
