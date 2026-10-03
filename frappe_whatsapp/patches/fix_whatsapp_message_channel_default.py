"""Make WhatsApp Message channel truly inherit WhatsApp Settings.

Frappe Select fields choose the first option when there is no blank option.
The previous field used ``Meta\nEvolution`` so Meta appeared even with an empty
default.  Add an explicit blank option and clear the Custom Field default so the
client can load WhatsApp Settings -> Default Channel.
"""

import frappe


def execute():
    name = frappe.db.get_value(
        "Custom Field",
        {"dt": "WhatsApp Message", "fieldname": "channel"},
        "name",
    )

    if name:
        frappe.db.set_value(
            "Custom Field",
            name,
            {
                "options": "\nMeta\nEvolution",
                "default": "",
                "description": "Blank uses WhatsApp Settings -> Default Channel.",
            },
            update_modified=False,
        )

    # Ensure every worker/web process reloads the updated field metadata.
    frappe.clear_cache(doctype="WhatsApp Message")
    frappe.clear_cache(doctype="WhatsApp Settings")
