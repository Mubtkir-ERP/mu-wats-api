"""Add channel routing + interactive fields to WhatsApp Message.

Meta remains the default so every existing screen and flow keeps working
exactly as before; Evolution is opt-in per message.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
    create_custom_fields(
        {
            "WhatsApp Message": [
                {
                    "fieldname": "channel",
                    "label": "Channel",
                    "fieldtype": "Select",
                    "options": "Meta\nEvolution",
                    "default": "Meta",
                    "insert_after": "message_type",
                    "in_list_view": 1,
                    "description": "Which platform sends this message. Meta = Cloud API, Evolution = Mubtkir API.",
                },
                {
                    "fieldname": "send_from_instance",
                    "label": "Send From Instance",
                    "fieldtype": "Link",
                    "options": "Whatsapp Instance",
                    "insert_after": "channel",
                    "depends_on": "eval:doc.channel=='Evolution'",
                    "description": "Evolution instance used to send. Defaults to the user's linked instance.",
                },
                {
                    "fieldname": "interactive_payload",
                    "label": "Interactive Payload",
                    "fieldtype": "JSON",
                    "insert_after": "message",
                    "depends_on": "eval:doc.channel=='Evolution'",
                    "description": "Optional buttons/list for Evolution. See whatsapp_message.py for the accepted shape.",
                },
            ]
        },
        ignore_validate=True,
    )
    frappe.db.commit()
