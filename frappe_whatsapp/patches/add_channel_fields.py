"""Add channel routing + interactive fields to WhatsApp Message.

The channel is intentionally left blank on new messages so the global Default
Channel in WhatsApp Settings controls routing. Existing messages keep their
stored channel value.
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
                    "options": "\nMeta\nEvolution",
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
                    "fieldname": "failure_reason",
                    "label": "Failure Reason",
                    "fieldtype": "Small Text",
                    "insert_after": "message_id",
                    "read_only": 1,
                    "description": "Last sending error, when the message failed.",
                },
                {
                    "fieldname": "read_receipt_sent",
                    "label": "Read Receipt Sent",
                    "fieldtype": "Check",
                    "default": "0",
                    "hidden": 1,
                    "read_only": 1,
                    "insert_after": "reply_to_message_id",
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
