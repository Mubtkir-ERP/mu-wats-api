"""Scheduled maintenance for WhatsApp data and temporary public files."""

import frappe
from frappe.utils import add_days, cint, now_datetime


def cleanup_old_whatsapp_data():
    """Apply WhatsApp Settings.retention_days and remove temporary public files."""
    settings = frappe.get_cached_doc("WhatsApp Settings", "WhatsApp Settings")
    retention_days = cint(settings.get("retention_days"))

    if retention_days > 0:
        cutoff = add_days(now_datetime(), -retention_days)
        # Message/log tables are append-heavy. The configured retention setting
        # now has real effect instead of growing these tables forever.
        frappe.db.delete("WhatsApp Notification Log", {"creation": ["<", cutoff]})
        frappe.db.delete("WhatsApp Message", {"creation": ["<", cutoff]})

    # Generated notification/report attachments must be public briefly so
    # Meta/Evolution can fetch them. Keep them for 48h, then delete both the
    # File row and its physical file via Frappe's normal document deletion.
    temp_cutoff = add_days(now_datetime(), -2)
    temp_files = frappe.get_all(
        "File",
        filters={
            "file_name": ["like", "wa-temp-%"],
            "creation": ["<", temp_cutoff],
        },
        pluck="name",
        limit_page_length=5000,
    )
    for name in temp_files:
        try:
            frappe.delete_doc("File", name, ignore_permissions=True, force=True)
        except Exception:
            frappe.log_error(frappe.get_traceback(), f"WhatsApp temp file cleanup failed: {name}")
