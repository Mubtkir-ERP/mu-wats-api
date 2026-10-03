"""Shared WhatsApp routing and normalisation helpers.

This module is intentionally small and dependency-light so every sending path
(manual messages, notifications, bulk campaigns and scheduled reports) follows
the same channel/instance/phone-number rules.
"""

from __future__ import annotations

import re

import frappe
from frappe import _

VALID_CHANNELS = ("Meta", "Evolution")


def get_settings():
    """Return the single WhatsApp Settings document."""
    return frappe.get_cached_doc("WhatsApp Settings", "WhatsApp Settings")


def is_enabled() -> bool:
    """Whether outbound WhatsApp sending is enabled globally."""
    settings = get_settings()
    # Old installations did not enforce this flag. The upgrade patch enables it
    # so existing users are not unexpectedly switched off.
    return bool(settings.get("enabled"))


def ensure_enabled():
    if not is_enabled():
        frappe.throw(
            _("WhatsApp sending is disabled in WhatsApp Settings."),
            title=_("WhatsApp Disabled"),
        )


def get_default_channel() -> str:
    """Return the configured default channel, with a safe Meta fallback."""
    channel = (get_settings().get("default_channel") or "Meta").strip()
    return channel if channel in VALID_CHANNELS else "Meta"


def resolve_channel(channel: str | None = None) -> str:
    channel = (channel or "").strip()
    if channel:
        if channel not in VALID_CHANNELS:
            frappe.throw(_("Unsupported WhatsApp channel: {0}").format(channel))
        return channel
    return get_default_channel()


def normalize_number(number) -> str:
    """Normalise a WhatsApp recipient number to international digits only.

    - strips spaces/punctuation and a leading ``00``
    - converts Saudi local mobile ``05XXXXXXXX`` to ``9665XXXXXXXX``
    - otherwise preserves the supplied country code
    """
    if number is None:
        return ""
    digits = re.sub(r"\D", "", str(number).strip())
    if digits.startswith("00"):
        digits = digits[2:]
    if len(digits) == 10 and digits.startswith("05"):
        digits = "966" + digits[1:]
    return digits


def resolve_default_instance(user: str | None = None) -> str | None:
    """Resolve the best Evolution instance for a user.

    Resolution order:
      1. instance linked to the requested/current user
      2. WhatsApp Settings -> Default Evolution Instance
      3. first connected instance
      4. first configured instance
    """
    user = user or getattr(getattr(frappe, "session", None), "user", None)
    if user and user != "Guest":
        linked = frappe.db.get_value("Whatsapp Instance", {"linked_user": user}, "name")
        if linked:
            return linked

    settings = get_settings()
    configured = settings.get("default_evolution_instance")
    if configured and frappe.db.exists("Whatsapp Instance", configured):
        return configured

    connected = frappe.db.get_value(
        "Whatsapp Instance", {"connection_status": "Connected"}, "name", order_by="modified desc"
    )
    if connected:
        return connected

    return frappe.db.get_value("Whatsapp Instance", {}, "name", order_by="modified desc")


def resolve_instance_name(instance_name: str | None = None, user: str | None = None) -> str:
    """Return an Evolution instance name or raise a clear configuration error."""
    name = instance_name or resolve_default_instance(user=user)
    if not name:
        frappe.throw(
            _(
                "No WhatsApp Evolution instance is available. Link an instance to the user "
                "or set Default Evolution Instance in WhatsApp Settings."
            )
        )
    if not frappe.db.exists("Whatsapp Instance", name):
        frappe.throw(_("WhatsApp instance {0} was not found.").format(frappe.bold(name)))
    return name


def default_server_name() -> str | None:
    """Return the configured default Evolution Server, or the first active one."""
    settings = get_settings()
    server = settings.get("default_server")
    if server and frappe.db.exists("Evolution Server", server):
        return server
    return frappe.db.get_value("Evolution Server", {"is_active": 1}, "name", order_by="modified desc")


def normalise_message_status(status: str | None) -> str | None:
    """Map Meta/Evolution status values to WhatsApp Message Select options."""
    if status is None:
        return None
    value = str(status).strip().lower()
    mapping = {
        "pending": "Pending",
        "queued": "Pending",
        "sent": "Sent",
        "server_ack": "Sent",
        "1": "Sent",
        "delivered": "Delivered",
        "delivery_ack": "Delivered",
        "2": "Delivered",
        "read": "Read",
        "played": "Read",
        "3": "Read",
        "4": "Read",
        "failed": "Failed",
        "error": "Failed",
        "received": "Received",
    }
    return mapping.get(value)


def can_advance_status(current: str | None, new: str | None) -> bool:
    """Do not downgrade Delivered/Read when late status events arrive."""
    if not new:
        return False
    if new == "Failed":
        return current not in ("Delivered", "Read")
    rank = {"Pending": 0, "Sent": 1, "Received": 1, "Delivered": 2, "Read": 3, "Failed": 0}
    return rank.get(new, 0) >= rank.get(current, 0)
