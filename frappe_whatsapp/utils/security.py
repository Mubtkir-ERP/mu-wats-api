"""Authorization at interactive boundaries; configured automation stays internal."""

import frappe
import re
from frappe import _
from contextlib import contextmanager


def require_sender(user=None):
    user = user or frappe.session.user
    if user == "Guest":
        frappe.throw(_("Sign in to send WhatsApp messages."), frappe.PermissionError)
    if set(frappe.get_roles(user)) & {"System Manager", "WhatsApp Manager"}:
        return
    frappe.has_permission("WhatsApp Message", "create", user=user, throw=True)


@contextmanager
def configured_automation():
    previous = frappe.flags.get("whatsapp_configured_automation")
    frappe.flags.whatsapp_configured_automation = True
    try:
        yield
    finally:
        frappe.flags.whatsapp_configured_automation = previous


def authorize_instance(name=None, user=None):
    from frappe_whatsapp.utils.messaging import resolve_instance_name

    user = user or frappe.session.user
    name = resolve_instance_name(name, user=user)
    doc = frappe.get_doc("Whatsapp Instance", name)
    # Unlinked instances are shared senders, deliberately available to all
    # authorized senders. This preserves the one-number / many-users workflow.
    if not doc.linked_user or doc.linked_user == user:
        return name
    if not frappe.has_permission("Whatsapp Instance", "write", doc=doc, user=user):
        frappe.throw(_("You are not authorized to use this WhatsApp instance."), frappe.PermissionError)
    return name


def shared_or_own_instance(user):
    # Frappe's 'is not set' handles both NULL and an empty Link value.
    shared = {"linked_user": ["is", "not set"]}
    connected = dict(shared, connection_status="Connected")
    return (
        frappe.db.get_value("Whatsapp Instance", connected, "name", order_by="modified desc")
        or frappe.db.get_value("Whatsapp Instance", shared, "name", order_by="modified desc")
    )


def log_remote(method, endpoint, status):
    # Never record query strings, media URLs, credentials, QR data or bodies.
    path = endpoint.split("?", 1)[0]
    frappe.logger().info("WhatsApp provider response: %s %s status=%s", method, path, status)


def provider_error(text, *credentials):
    value = str(text or "")
    for credential in credentials:
        if credential:
            value = value.replace(str(credential), "[redacted]")
    return re.sub(r'(?i)("?(?:api_?key|token|secret|authorization)"?\s*[:=]\s*)("[^"]*"|\S+)', r'\1"[redacted]"', value)[:1000]
