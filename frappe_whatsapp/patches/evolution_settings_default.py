"""Apply the requested default once on existing sites and retire the old field."""

import frappe


def execute():
    settings = frappe.get_single("WhatsApp Settings")
    settings.default_channel = "Evolution"
    settings.save(ignore_permissions=True)
    # The field is removed from the standard DocType. Remove its stale Single
    # value as well so it cannot reappear in settings exports or legacy reads.
    frappe.db.sql(
        "DELETE FROM `tabSingles` WHERE doctype=%s AND field=%s",
        ("WhatsApp Settings", "default_evolution_instance"),
    )
    frappe.clear_cache(doctype="WhatsApp Settings")
