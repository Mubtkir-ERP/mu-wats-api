"""Force-resync the WhatsApp workspace so the Contact Puller link appears.

Frappe skips re-importing a Workspace when the file's ``modified`` timestamp
is not newer than the DB copy, so the newly added "WhatsApp Contact Puller"
link never reached existing sites. Forcing the reload applies it.
"""

import frappe


def execute():
	frappe.reload_doc("frappe_whatsapp", "workspace", "whatsapp", force=True)
	frappe.clear_cache()
