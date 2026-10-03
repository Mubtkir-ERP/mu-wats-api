"""Upgrade routing defaults and secure legacy Evolution credentials."""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def _safe_meta_token(settings):
    try:
        return settings.get_password("token", raise_exception=False) or ""
    except Exception:
        return ""


def execute():
    # Ensure the message routing fields also exist on sites where the older
    # add_channel_fields patch was partially applied.
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
                    "description": "Blank uses WhatsApp Settings -> Default Channel.",
                },
                {
                    "fieldname": "send_from_instance",
                    "label": "Send From Instance",
                    "fieldtype": "Link",
                    "options": "Whatsapp Instance",
                    "insert_after": "channel",
                    "depends_on": "eval:doc.channel=='Evolution'",
                },
                {
                    "fieldname": "failure_reason",
                    "label": "Failure Reason",
                    "fieldtype": "Small Text",
                    "insert_after": "message_id",
                    "read_only": 1,
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
                },
            ]
        },
        ignore_validate=True,
    )

    # A fixed Custom Field default of Meta bypasses the new global routing
    # setting. Leave it blank so WhatsAppMessage resolves the configured channel.
    cf = frappe.db.get_value(
        "Custom Field", {"dt": "WhatsApp Message", "fieldname": "channel"}, "name"
    )
    if cf:
        frappe.db.set_value("Custom Field", cf, "default", "")

    settings = frappe.get_single("WhatsApp Settings")

    # The old Enabled checkbox was never enforced. Enable it during the upgrade
    # so introducing enforcement does not unexpectedly stop an existing site.
    if not settings.enabled:
        settings.enabled = 1

    # Pick a sensible routing default for existing installations:
    # - Evolution when an instance exists and no usable Meta token exists
    # - Meta when a usable Meta token exists
    # - otherwise preserve/initialise Meta.
    token = _safe_meta_token(settings)
    has_evolution = bool(frappe.db.count("Whatsapp Instance"))
    if has_evolution and not token:
        settings.default_channel = "Evolution"
    elif token:
        settings.default_channel = "Meta"
    elif not settings.default_channel:
        settings.default_channel = "Meta"

    if not settings.default_evolution_instance and has_evolution:
        settings.default_evolution_instance = (
            frappe.db.get_value(
                "Whatsapp Instance", {"connection_status": "Connected"}, "name", order_by="modified desc"
            )
            or frappe.db.get_value("Whatsapp Instance", {}, "name", order_by="modified desc")
        )

    if not settings.default_server:
        settings.default_server = frappe.db.get_value(
            "Evolution Server", {"is_active": 1}, "name", order_by="modified desc"
        )

    settings.save(ignore_permissions=True)

    # Evolution Phone Settings is legacy but may still exist on old sites.
    # Its API key field is now Password; migrate any plaintext value into the
    # encrypted password store without exposing it in list views.
    if frappe.db.table_exists("Evolution Phone Settings"):
        rows = frappe.db.sql(
            "SELECT name, global_api_key FROM `tabEvolution Phone Settings`",
            as_dict=True,
        )
        for row in rows:
            raw = row.get("global_api_key")
            if not raw or raw == "********":
                continue
            try:
                doc = frappe.get_doc("Evolution Phone Settings", row.name)
                doc.global_api_key = raw
                doc.save(ignore_permissions=True)
            except Exception:
                frappe.log_error(
                    frappe.get_traceback(),
                    f"Could not migrate Evolution Phone Settings key: {row.name}",
                )

    frappe.clear_cache(doctype="WhatsApp Settings")
    frappe.db.commit()
