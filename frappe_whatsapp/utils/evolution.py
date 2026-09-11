"""Unified Evolution (Mubtkir API) send layer.

Single source of truth for every outbound Evolution request. Previously the
send logic was duplicated across whatsapp_notification.py, bulk_whatsapp_message.py
and whatsapp_scheduled_report.py. Those callers should now use these helpers.

Credentials are always resolved from the "Evolution Server" doctype through
api._server_credentials — never hardcoded.

Endpoints used (Evolution API v2):
    POST /message/sendText/{instance}
    POST /message/sendMedia/{instance}
    POST /message/sendButtons/{instance}   (interactive reply buttons, up to 3)
    POST /message/sendList/{instance}       (interactive list, up to 10 rows)
"""

import frappe
from frappe import _

from frappe_whatsapp.api import _request, _server_credentials


def resolve_instance(instance_name=None, user=None):
    """Return (base_url, api_key, instance_name).

    Resolution order:
      1. explicit instance_name argument
      2. the Whatsapp Instance linked to `user` (defaults to session user)
    """
    if not instance_name:
        user = user or frappe.session.user
        instance_name = frappe.db.get_value(
            "Whatsapp Instance", {"linked_user": user}, "name"
        )
    if not instance_name:
        frappe.throw(_("No WhatsApp instance to send from. Set the sending instance."))

    server = frappe.db.get_value("Whatsapp Instance", instance_name, "evolution_server")
    base_url, api_key = _server_credentials(server)
    return base_url, api_key, instance_name


def _clean_number(number):
    """Strip a leading + so Evolution accepts the MSISDN."""
    number = (number or "").strip()
    if number.startswith("+"):
        number = number[1:]
    return number


def _extract_message_id(response):
    """Pull the WhatsApp message id out of an Evolution send response."""
    if not isinstance(response, dict):
        return ""
    mid = response.get("key", {}).get("id", "")
    if not mid:
        mid = response.get("message", {}).get("key", {}).get("id", "")
    return mid or ""


def send_text(number, text, instance_name=None, base_url=None, api_key=None):
    """Send a plain text message. Returns the message id."""
    if not base_url:
        base_url, api_key, instance_name = resolve_instance(instance_name)
    payload = {"number": _clean_number(number), "text": text or "No Text"}
    response = _request(
        "POST", base_url, f"/message/sendText/{instance_name}", api_key, payload
    )
    return _extract_message_id(response)


def send_media(number, media_url, mediatype="image", caption=None, filename=None,
               mimetype=None, instance_name=None, base_url=None, api_key=None):
    """Send an image / document / video / audio by URL. Returns the message id.

    mediatype: one of "image", "document", "video", "audio".
    """
    if not base_url:
        base_url, api_key, instance_name = resolve_instance(instance_name)
    payload = {
        "number": _clean_number(number),
        "mediatype": mediatype,
        "media": media_url,
    }
    if caption:
        payload["caption"] = caption
    if mediatype == "document":
        payload["fileName"] = filename or "file"
        payload["mimetype"] = mimetype or "application/octet-stream"
    response = _request(
        "POST", base_url, f"/message/sendMedia/{instance_name}", api_key, payload
    )
    return _extract_message_id(response)


def send_buttons(number, title, description, buttons, footer=None,
                 instance_name=None, base_url=None, api_key=None):
    """Send up to 3 interactive reply buttons. Returns the message id.

    buttons: list of dicts. Each accepts:
        {"id": "opt_1", "title": "Option 1"}
    """
    if not base_url:
        base_url, api_key, instance_name = resolve_instance(instance_name)

    formatted = []
    for i, b in enumerate(buttons[:3]):
        formatted.append({
            "type": "reply",
            "displayText": b.get("title") or b.get("displayText") or f"Option {i + 1}",
            "id": str(b.get("id") or i + 1),
        })

    payload = {
        "number": _clean_number(number),
        "title": title or "",
        "description": description or "",
        "buttons": formatted,
    }
    if footer:
        payload["footer"] = footer

    response = _request(
        "POST", base_url, f"/message/sendButtons/{instance_name}", api_key, payload
    )
    return _extract_message_id(response)


def send_list(number, title, description, button_text, sections,
              footer=None, instance_name=None, base_url=None, api_key=None):
    """Send an interactive list (up to 10 rows across sections). Returns message id.

    sections: list of dicts:
        {"title": "Section", "rows": [
            {"id": "row_1", "title": "Row 1", "description": "optional"}
        ]}
    """
    if not base_url:
        base_url, api_key, instance_name = resolve_instance(instance_name)

    formatted_sections = []
    for sec in sections:
        rows = []
        for i, r in enumerate(sec.get("rows", [])):
            rows.append({
                "rowId": str(r.get("id") or i + 1),
                "title": r.get("title") or f"Row {i + 1}",
                "description": r.get("description", ""),
            })
        formatted_sections.append({
            "title": sec.get("title", ""),
            "rows": rows,
        })

    payload = {
        "number": _clean_number(number),
        "title": title or "",
        "description": description or "",
        "buttonText": button_text or "Select",
        "sections": formatted_sections,
    }
    if footer:
        payload["footer"] = footer

    response = _request(
        "POST", base_url, f"/message/sendList/{instance_name}", api_key, payload
    )
    return _extract_message_id(response)
