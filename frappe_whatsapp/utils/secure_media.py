"""Short-lived, signed download capability for private provider attachments."""

import hashlib
import hmac
import mimetypes
import time
from urllib.parse import urlencode, urlsplit, unquote

import frappe
from frappe import _

TTL = 900


def _secret():
    key = frappe.local.conf.get("encryption_key")
    if not key:
        frappe.throw(_("Site encryption key is required for private WhatsApp attachments."))
    return key.encode()


def _signature(name, expires):
    return hmac.new(_secret(), f"whatsapp-media-v1\0{name}\0{expires}".encode(), hashlib.sha256).hexdigest()


def provider_url(value):
    if not value:
        return value
    from frappe.utils import get_url

    url = str(value)
    parsed = urlsplit(url)
    local = urlsplit(get_url())
    if parsed.netloc and parsed.netloc != local.netloc:
        return url
    path = unquote(parsed.path)
    if not path.startswith("/private/files/"):
        return url if parsed.netloc else get_url() + "/" + url.lstrip("/")
    name = frappe.db.get_value("File", {"file_url": path}, "name")
    if not name:
        frappe.throw(_("The private attachment was not found."))
    file = frappe.get_doc("File", name)
    file.check_permission("read")
    expires = int(time.time()) + TTL
    query = urlencode({"file": name, "expires": expires, "signature": _signature(name, expires)})
    return get_url() + "/api/method/frappe_whatsapp.utils.secure_media.download?" + query


@frappe.whitelist(allow_guest=True, methods=["GET"])
def download(file, expires, signature):
    from werkzeug.wrappers import Response

    try:
        expiry = int(expires)
    except (ValueError, TypeError):
        frappe.throw(_("Invalid media link."), frappe.PermissionError)
    now = int(time.time())
    if expiry < now or expiry > now + TTL or not hmac.compare_digest(_signature(file, expiry).encode(), str(signature).encode()):
        frappe.throw(_("Expired or invalid media link."), frappe.PermissionError)
    doc = frappe.get_doc("File", file)
    if not doc.is_private:
        frappe.throw(_("Invalid media file."), frappe.PermissionError)
    # Signature grants only this File for this limited period, not generic read.
    response = Response(doc.get_content(), content_type=mimetypes.guess_type(doc.file_name or "")[0] or "application/octet-stream")
    response.headers["Cache-Control"] = "private, no-store, max-age=0"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response
