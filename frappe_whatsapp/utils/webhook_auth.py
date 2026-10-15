"""Authenticate before parsing/processing and bound media fetches."""
import hashlib
import hmac
import json
import re
from urllib.parse import urlsplit

import frappe
import requests

from frappe_whatsapp.utils.operations import claim_event


def password(settings, field):
    try:
        value = settings.get_password(field, raise_exception=False) or ""
    except TypeError:
        if not settings.get(field):
            return ""
        value = settings.get_password(field) or ""
    if not value and settings.get(field):
        frappe.throw("Unable to decrypt configured webhook credentials", frappe.PermissionError)
    return value


def payload():
    settings = frappe.get_cached_doc("WhatsApp Settings", "WhatsApp Settings")
    # Allow the configured file size including base64 overhead used by Evo.
    limit = max(2 * 1024 * 1024, int(settings.get("max_attachment_size") or 16) * 1024 * 1024 * 4 // 3 + 1024 * 1024)
    if len(frappe.request.get_data()) > limit:
        frappe.throw("Webhook payload is too large", frappe.ValidationError)
    data = frappe.request.get_json(silent=True)
    if not isinstance(data, dict):
        frappe.throw("Expected a JSON webhook object", frappe.ValidationError)
    return data


def header(name):
    return frappe.get_request_header(name) or ""


def interactive_text(value):
    if value.get("nfm_reply"):
        return value["nfm_reply"].get("response_json") or ""
    reply = value.get("button_reply") or value.get("list_reply") or {}
    return reply.get("title") or reply.get("id") or ""


def meta_payload():
    settings = frappe.get_doc("WhatsApp Settings", "WhatsApp Settings")
    secret = password(settings, "meta_app_secret")
    signature = "sha256=" + hmac.new(secret.encode(), frappe.request.get_data(), hashlib.sha256).hexdigest()
    if not secret or not hmac.compare_digest(signature.encode(), header("X-Hub-Signature-256").encode()):
        frappe.throw("Invalid Meta signature. Configure Meta App Secret.", frappe.PermissionError)
    return payload()


def evolution(data, instance):
    if not instance or not frappe.db.exists("Whatsapp Instance", instance):
        frappe.throw("Unknown Evolution webhook instance", frappe.PermissionError)
    settings = frappe.get_doc("WhatsApp Settings", "WhatsApp Settings")
    secret = password(settings, "evolution_webhook_secret")
    bearer = header("Authorization")
    bearer = bearer[7:].strip() if bearer.lower().startswith("bearer ") else ""
    if secret:
        provided = header("X-Webhook-Secret") or header("X-Evolution-Webhook-Secret") or bearer or str(frappe.form_dict.get("secret") or "")
        valid = bool(provided) and hmac.compare_digest(str(secret).encode(), str(provided).encode())
    else:
        doc = frappe.get_doc("Whatsapp Instance", instance)
        server = frappe.get_doc("Evolution Server", doc.evolution_server)
        expected = server.get_api_key()
        provided = header("apikey") or header("X-API-Key") or str(data.get("apikey") or "")
        valid = bool(expected and provided) and hmac.compare_digest(str(expected).encode(), str(provided).encode())
    if not valid:
        frappe.throw("Invalid Evolution webhook credentials. Configure shared secret or server apikey.", frappe.PermissionError)
    return instance


def event_once(channel, scope, data):
    digest = hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()
    return claim_event("webhook-event", f"{channel}:{scope}:{digest}")


def fetch_meta_media(media_id, settings):
    import mimetypes
    if not re.fullmatch(r"[0-9]+", str(media_id or "")):
        frappe.throw("Invalid Meta media ID")
    headers = {"Authorization": "Bearer " + password(settings, "token")}
    meta = requests.get(f"{settings.url.rstrip('/')}/{settings.version}/{media_id}", headers=headers, timeout=30, allow_redirects=False)
    meta.raise_for_status()
    info = meta.json()
    url = urlsplit(info.get("url") or "")
    host = (url.hostname or "").lower()
    if url.scheme != "https" or not any(host == d or host.endswith("." + d) for d in ("facebook.com", "fbsbx.com", "fbcdn.net")):
        frappe.throw("Untrusted Meta media URL")
    limit = int(settings.get("max_attachment_size") or 16) * 1024 * 1024
    with requests.get(info["url"], headers=headers, timeout=30, allow_redirects=False, stream=True) as response:
        response.raise_for_status()
        content = bytearray()
        for chunk in response.iter_content(64 * 1024):
            content.extend(chunk)
            if len(content) > limit:
                frappe.throw("WhatsApp attachment exceeds configured size limit")
    extension = mimetypes.guess_extension(info.get("mime_type") or "") or ".bin"
    return frappe.generate_hash(length=16) + extension, bytes(content)
