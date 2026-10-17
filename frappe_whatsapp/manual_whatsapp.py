"""Manually retry the configured document notification, using its exact template."""

import frappe
from frappe import _

SUPPORTED = ("Sales Invoice", "Quotation")


@frappe.whitelist()
def send_document_message(doctype, name):
    """Send via the existing automatic notification pipeline, without text overrides."""
    if doctype not in SUPPORTED:
        frappe.throw(_("Unsupported document type"))
    doc = frappe.get_doc(doctype, name)
    doc.check_permission("read")
    if doc.is_new():
        frappe.throw(_("Save the document before sending."))
    # Require permission to create an outbound message, rather than using the
    # privileged background notification context for arbitrary read-only users.
    if not frappe.has_permission("WhatsApp Message", "create"):
        frappe.throw(_("Not permitted to send WhatsApp messages"), frappe.PermissionError)

    notifications = frappe.get_all(
        "WhatsApp Notification",
        filters={"reference_doctype": doctype, "disabled": 0,
                 "notification_type": "DocType Event"},
        fields=["name", "doctype_event"],
    )
    # Match the lifecycle event relevant to this particular document.
    preferred = ("After Submit", "Before Submit") if doc.docstatus == 1 else (
        ("After Cancel", "Before Cancel") if doc.docstatus == 2 else
        ("After Save", "After Insert", "Before Save")
    )
    matches = [item for item in notifications if item.doctype_event in preferred]
    if not matches:
        frappe.throw(_("No active WhatsApp Notification for this document status. Configure an automatic notification first."))
    # An ambiguous choice could send an incorrect template, so don't silently
    # select one of several enabled notification templates.
    if len(matches) != 1:
        frappe.throw(_("Multiple WhatsApp Notifications match this document. Disable the extra notifications or keep one matching notification."))

    notification = frappe.get_doc("WhatsApp Notification", matches[0].name)
    if not notification.template:
        frappe.throw(_("The WhatsApp Notification does not have a template."))
    # Bypass the triggering event's condition for a requested retry, while
    # preserving the notification's content, field substitution, recipient,
    # attachments, print format, instance, and channel routing.
    result = notification.send_template_message(doc, ignore_condition=True)
    if not result:
        frappe.throw(_("WhatsApp message was not created."))
    return {"name": result.name, "status": result.status, "channel": result.channel}
