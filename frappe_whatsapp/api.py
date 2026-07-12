# Copyright (c) 2026, Shridhar Patil and contributors
# For license information, please see license.txt
"""Whitelisted API for WhatsApp instance management via the Mubtkir API.

The Mubtkir API base URL and API key are always resolved from the
``Mubtkir API Server`` doctype and are never hardcoded here.
"""

import re
import traceback

import requests

import frappe
from frappe import _

# Timeout (in seconds) for every outbound Mubtkir API request.
REQUEST_TIMEOUT = 30

# Mapping of Mubtkir API connection states to our stored status values.
STATE_MAP = {
	"open": "Connected",
	"connecting": "Connecting",
	"close": "Disconnected",
	"closed": "Disconnected",
}


def map_state(state):
	"""Map an Mubtkir API state to our status.

	``open`` -> Connected, ``connecting`` -> Connecting, anything else ->
	Disconnected.
	"""
	return STATE_MAP.get((state or "").lower(), "Disconnected")


def _server_credentials(server_name):
	"""Return ``(base_url, api_key)`` for the given Mubtkir API Server."""
	if not server_name:
		frappe.throw(_("No Mubtkir API Server is configured for this instance."))

	server = frappe.get_doc("Evolution Server", server_name)
	# Normalise the base URL: strip any trailing slash so paths join cleanly.
	# api_key is a Password field, so read it via get_password (not the attribute).
	base_url = server.get_base_url()
	api_key = server.get_api_key()

	# Log which server / base_url is actually being used. Only the first 20
	# characters of the URL are logged, for security.
	frappe.logger().error(
		f"Mubtkir API Server in use: {server_name} | base_url={base_url[:20]!r} | "
		f"api_key_set={bool(api_key)}"
	)

	if not base_url or not api_key:
		frappe.throw(
			_("Mubtkir API Server {0} is missing its Base URL or API Key.").format(
				frappe.bold(server_name)
			)
		)

	return base_url, api_key


def _headers(api_key):
	"""Build the standard Mubtkir API request headers."""
	return {"apikey": api_key, "Content-Type": "application/json"}


def _request(method, base_url, path, api_key, payload=None):
	"""Perform an Mubtkir API request and return the parsed JSON body.

	Logs the full request/response (or the traceback on a transport error) and
	surfaces the *actual* error to the user instead of a generic message, so
	failures (401/404/timeouts/DNS) can be diagnosed.
	"""
	url = f"{base_url}{path}"

	# --- Transport layer: connection refused, DNS failure, timeout, TLS ... ---
	try:
		response = requests.request(
			method,
			url,
			headers=_headers(api_key),
			json=payload,
			timeout=REQUEST_TIMEOUT,
		)
	except Exception as exc:
		tb = traceback.format_exc()
		frappe.logger().error(f"Mubtkir API connection error [{method} {url}]:\n{tb}")
		frappe.log_error(message=f"{method} {url}\n{tb}", title="Mubtkir API Connection Error")
		frappe.throw(
			_("Mubtkir API error: {0}").format(str(exc)),
			title=_("Connection Error"),
		)

	# --- Always log the raw response so we can see exactly what came back. ---
	frappe.logger().error(
		f"Mubtkir API response [{method} {url}]: {response.status_code} — {response.text}"
	)

	# --- HTTP layer: 4xx / 5xx. Surface the status + body, don't hide it. ---
	if response.status_code >= 400:
		frappe.log_error(
			message=f"{method} {url}\n{response.status_code} {response.text}",
			title="Mubtkir API HTTP Error",
		)
		frappe.throw(
			_("Mubtkir API error: {0} — {1}").format(
				response.status_code, (response.text or "")[:500]
			),
			title=_("Mubtkir API Error"),
		)

	if response.content:
		try:
			return response.json()
		except ValueError:
			return {}
	return {}


def _extract_api_key(data):
	"""Extract the per-instance API key from a create response.

	The Mubtkir API returns the key under ``hash`` (v2, a string) or under
	``hash.apikey`` (v1, an object).
	"""
	hash_value = data.get("hash")
	if isinstance(hash_value, dict):
		return hash_value.get("apikey")
	if isinstance(hash_value, str):
		return hash_value
	return None


def _extract_qr(data):
	"""Extract a base64 QR image from a connect response.

	Handles both the flat form (``base64``) and the nested form
	(``qrcode.base64`` / ``qrcode`` as a string).
	"""
	base64 = data.get("base64")
	if not base64:
		qrcode = data.get("qrcode")
		if isinstance(qrcode, dict):
			base64 = qrcode.get("base64")
		elif isinstance(qrcode, str):
			base64 = qrcode
	return base64


def _extract_phone_from_jid(owner_jid):
	"""Return the digits before ``@`` in an ownerJid (e.g. 9665...@s.whatsapp.net)."""
	if not owner_jid or "@" not in owner_jid:
		return ""
	number = owner_jid.split("@")[0]
	return re.sub(r"\D", "", number)


def _fetch_owner_jid(base_url, api_key, instance_name):
	"""Best-effort lookup of an instance's ownerJid via /instance/fetchInstances.

	The Mubtkir API response shape varies between versions, so this tolerates
	both a bare list and a ``{"data": [...]}`` wrapper, and both flat and
	``{"instance": {...}}`` records. Returns "" if nothing matches.
	"""
	try:
		data = _request("GET", base_url, "/instance/fetchInstances", api_key)
	except Exception:
		return ""

	records = data if isinstance(data, list) else (data.get("data") or data.get("instances") or [])
	for rec in records:
		if not isinstance(rec, dict):
			continue
		info = rec.get("instance") if isinstance(rec.get("instance"), dict) else rec
		name = info.get("instanceName") or info.get("name")
		if name == instance_name:
			return info.get("ownerJid") or info.get("owner") or ""
	return ""


def _get_instance(instance_name):
	"""Load a Whatsapp Instance document with a read permission check."""
	if not frappe.db.exists("Whatsapp Instance", instance_name):
		frappe.throw(_("WhatsApp instance {0} was not found.").format(frappe.bold(instance_name)))

	doc = frappe.get_doc("Whatsapp Instance", instance_name)
	doc.check_permission("read")
	return doc


def _store_status(instance_name, status, doc=None):
	"""Persist ``connection_status`` (and connected_since) via db.set_value."""
	values = {"connection_status": status}

	current_connected_since = doc.connected_since if doc else frappe.db.get_value(
		"Whatsapp Instance", instance_name, "connected_since"
	)

	if status == "Connected" and not current_connected_since:
		values["connected_since"] = frappe.utils.now_datetime()
	elif status == "Disconnected" and current_connected_since:
		values["connected_since"] = None

	frappe.db.set_value("Whatsapp Instance", instance_name, values)
	frappe.db.commit()
	return status


def _normalise_base_url(raw):
	"""Strip whitespace / trailing slash and ensure an http(s) scheme."""
	base_url = (raw or "").strip().rstrip("/")
	if base_url and not base_url.startswith(("http://", "https://")):
		base_url = "https://" + base_url
	return base_url


@frappe.whitelist()
def is_registered(instance_name):
	"""Return True if the instance has been registered on the Mubtkir API.

	Registration stores a per-instance ``api_key``, so a non-empty key is the
	marker for "already created in Mubtkir API". Used by the form to decide
	whether to show the "Create Instance" button or the "Show QR Code" button.
	"""
	if not frappe.db.exists("Whatsapp Instance", instance_name):
		return False
	doc = frappe.get_doc("Whatsapp Instance", instance_name)
	return bool(doc.get_password("api_key", raise_exception=False))


@frappe.whitelist()
def create_whatsapp_instance(instance_name):
	"""Register an existing Whatsapp Instance record on the Mubtkir API.

	The local record already exists (its name was auto-generated on save); this
	registers that same name on the Mubtkir API, stores the returned
	per-instance API key and sets the status to Disconnected (ready for QR).
	Returns ``{"success": True, "api_key": ...}``.
	"""
	if not frappe.db.exists("Whatsapp Instance", instance_name):
		frappe.throw(
			_("WhatsApp instance {0} was not found.").format(frappe.bold(instance_name))
		)

	doc = frappe.get_doc("Whatsapp Instance", instance_name)
	doc.check_permission("write")

	if not doc.evolution_server:
		frappe.throw(_("Please set an Mubtkir API Server on this instance first."))

	server = frappe.get_doc("Evolution Server", doc.evolution_server)

	base_url = _normalise_base_url(server.base_url)
	server_api_key = server.get_password("api_key", raise_exception=False)
	url = f"{base_url}/instance/create"

	frappe.logger().error(
		f"Registering Mubtkir API instance '{instance_name}' on {base_url[:20]!r}"
	)
	try:
		response = requests.post(
			url,
			headers={"apikey": server_api_key, "Content-Type": "application/json"},
			json={"instanceName": instance_name, "integration": "WHATSAPP-BAILEYS"},
			timeout=REQUEST_TIMEOUT,
		)
	except Exception as exc:
		frappe.logger().error(
			f"Mubtkir API connection error [POST {url}]:\n{traceback.format_exc()}"
		)
		frappe.throw(
			_("Mubtkir API error: {0}").format(str(exc)),
			title=_("Connection Error"),
		)

	frappe.logger().error(
		f"Mubtkir API response [POST {url}]: {response.status_code} — {response.text}"
	)
	if response.status_code not in (200, 201):
		frappe.throw(
			_("Mubtkir API error: {0} — {1}").format(
				response.status_code, (response.text or "")[:500]
			),
			title=_("Mubtkir API Error"),
		)

	data = response.json() if response.content else {}
	instance_key = _extract_api_key(data) or data.get("apikey") or ""

	frappe.db.set_value(
		"Whatsapp Instance",
		instance_name,
		{"api_key": instance_key, "connection_status": "Disconnected"},
	)
	frappe.db.commit()

	return {"success": True, "api_key": instance_key}


def delete_remote_instance(doc):
	"""Best-effort removal of an instance from its Mubtkir API server.

	Calls ``DELETE /instance/delete/{name}``. Any failure (the remote may
	already be gone, or the server unreachable) is logged and swallowed so it
	never blocks removal of the local record. Does nothing when no Mubtkir API
	server is set.
	"""
	if not getattr(doc, "evolution_server", None):
		return
	try:
		base_url, api_key = _server_credentials(doc.evolution_server)
		_request("DELETE", base_url, f"/instance/delete/{doc.name}", api_key)
	except Exception:
		frappe.log_error(
			message=frappe.get_traceback(),
			title=f"Mubtkir API remote delete failed for {doc.name}",
		)


@frappe.whitelist()
def delete_whatsapp_instance(instance_name, delete_remote=False):
	"""Cleanup helper: remove a WhatsApp instance record (and optionally remote).

	Useful for removing stale/half-created instances (e.g. ``develop2-37757``).
	When ``delete_remote`` is truthy, also calls ``DELETE /instance/delete/{name}``
	on the Mubtkir API first. Missing local records are treated as already
	cleaned up.

	Deleting the local document normally triggers ``on_trash`` which removes the
	remote too; this helper drives that behaviour explicitly via
	``wa_skip_remote_delete`` so the remote is only touched when asked for.
	"""
	if not frappe.has_permission("Whatsapp Instance", "delete"):
		frappe.throw(_("You are not permitted to delete WhatsApp instances."))

	if not frappe.db.exists("Whatsapp Instance", instance_name):
		return {"deleted": False, "message": f"No local record named {instance_name}."}

	if frappe.utils.cint(delete_remote):
		doc = frappe.get_doc("Whatsapp Instance", instance_name)
		delete_remote_instance(doc)

	# The remote has already been handled above (or intentionally kept), so tell
	# on_trash not to repeat the DELETE call.
	frappe.flags.wa_skip_remote_delete = True
	try:
		frappe.delete_doc("Whatsapp Instance", instance_name, ignore_permissions=True)
	finally:
		frappe.flags.wa_skip_remote_delete = False
	frappe.db.commit()
	return {"deleted": True, "message": f"Deleted {instance_name}."}


def _fetch_remote_instance_names(base_url, api_key):
	"""Return the set of instance names currently present on an Mubtkir API server.

	Tolerates the response-shape differences between Mubtkir API versions
	(bare list vs ``{"data": [...]}``; flat record vs ``{"instance": {...}}``).
	Raises to the caller if the server cannot be reached, so a transient outage
	is never mistaken for "all instances were deleted".
	"""
	data = _request("GET", base_url, "/instance/fetchInstances", api_key)
	records = data if isinstance(data, list) else (data.get("data") or data.get("instances") or [])
	names = set()
	for rec in records:
		if not isinstance(rec, dict):
			continue
		info = rec.get("instance") if isinstance(rec.get("instance"), dict) else rec
		name = info.get("instanceName") or info.get("name")
		if name:
			names.add(name)
	return names


def _clear_registration(instance_name):
	"""Reset a local instance so it looks unregistered and can be re-created.

	Clears the per-instance ``api_key`` (so ``is_registered`` returns False and
	the form shows the "Create Instance" button again), marks it Disconnected and
	wipes ``connected_since``.
	"""
	frappe.db.set_value(
		"Whatsapp Instance",
		instance_name,
		{
			"connection_status": "Disconnected",
			"api_key": "",
			"connected_since": None,
		},
	)
	frappe.db.commit()


@frappe.whitelist()
def sync_instance_with_evolution(instance_name):
	"""Reconcile a single local instance against its Mubtkir API server.

	If the instance no longer exists on Mubtkir API (it was deleted there), the
	local record is kept but its registration is cleared so the user can
	re-create it under the same name. If it still exists, its live connection
	status is refreshed. Returns ``{"exists": bool, "status": <status>}``.
	"""
	doc = _get_instance(instance_name)
	doc.check_permission("write")

	if not doc.evolution_server:
		frappe.throw(_("Please set an Mubtkir API Server on this instance first."))

	base_url, api_key = _server_credentials(doc.evolution_server)

	try:
		remote_names = _fetch_remote_instance_names(base_url, api_key)
	except Exception:
		# Could not reach Mubtkir API — do NOT clear the record on a transient error.
		frappe.throw(
			_("Could not reach the Mubtkir API server to sync. Please try again."),
			title=_("Sync Failed"),
		)

	if instance_name not in remote_names:
		_clear_registration(instance_name)
		return {"exists": False, "status": "Disconnected"}

	# Still present remotely — refresh the live status/phone number.
	result = get_instance_status(instance_name)
	return {"exists": True, "status": result.get("status")}


@frappe.whitelist()
def sync_server_instances(server_name):
	"""Reconcile every local instance hosted on ``server_name`` against Mubtkir API.

	Any local instance missing from the server has its registration cleared (see
	``_clear_registration``). Returns ``{"checked": n, "missing": [...]}``.
	"""
	if not frappe.has_permission("Evolution Server", "write"):
		frappe.throw(_("You are not permitted to sync Mubtkir API servers."))

	server = frappe.get_doc("Evolution Server", server_name)
	base_url = server.get_base_url()
	api_key = server.get_api_key()
	if not base_url or not api_key:
		frappe.throw(
			_("Mubtkir API Server {0} is missing its Base URL or API Key.").format(
				frappe.bold(server_name)
			)
		)

	try:
		remote_names = _fetch_remote_instance_names(base_url, api_key)
	except Exception:
		frappe.throw(
			_("Could not reach the Mubtkir API server to sync. Please try again."),
			title=_("Sync Failed"),
		)

	local = frappe.get_all(
		"Whatsapp Instance",
		filters={"evolution_server": server_name},
		pluck="name",
	)
	missing = [name for name in local if name not in remote_names]
	for name in missing:
		_clear_registration(name)

	return {"checked": len(local), "missing": missing}


@frappe.whitelist()
def check_user_instance(user):
	"""Return the name of the existing instance for ``user``, if any."""
	if not user:
		return None

	return frappe.db.get_value("Whatsapp Instance", {"linked_user": user}, "name")


@frappe.whitelist()
def get_qr_code(instance_name):
	"""Fetch a QR code from ``GET /instance/connect/{name}``.

	Returns a dict containing (when available) ``base64``, ``code`` and the
	current ``status``.
	"""
	doc = _get_instance(instance_name)
	base_url, api_key = _server_credentials(doc.evolution_server)

	frappe.logger().error(f"Fetching QR for instance '{instance_name}' on {base_url[:20]!r}")
	data = _request("GET", base_url, f"/instance/connect/{instance_name}", api_key)

	# When already connected the API returns the state instead of a QR payload.
	state = (data.get("instance") or {}).get("state")
	if state:
		status = _store_status(instance_name, map_state(state), doc)
	else:
		status = _store_status(instance_name, "Connecting", doc)

	return {
		"base64": _extract_qr(data),
		"code": data.get("code") or data.get("pairingCode"),
		"status": status,
	}


@frappe.whitelist()
def get_instance_status(instance_name):
	"""Fetch state from ``GET /instance/connectionState/{name}``.

	Maps the state and updates ``connection_status`` in the DB. When the
	instance becomes Connected, the WhatsApp phone number is extracted from the
	``ownerJid`` (falling back to /instance/fetchInstances) and stored.

	Returns ``{"status": <status>, "phone_number": <digits>}``.
	"""
	doc = _get_instance(instance_name)
	base_url, api_key = _server_credentials(doc.evolution_server)

	data = _request("GET", base_url, f"/instance/connectionState/{instance_name}", api_key)
	state = (data.get("instance") or {}).get("state") or data.get("state")
	status = map_state(state)

	phone_number = doc.phone_number or ""

	if status == "Connected":
		owner_jid = (data.get("instance") or {}).get("ownerJid") or data.get("ownerJid") or ""
		if not owner_jid:
			owner_jid = _fetch_owner_jid(base_url, api_key, instance_name)
		extracted = _extract_phone_from_jid(owner_jid)
		if extracted:
			phone_number = extracted

		values = {"connection_status": "Connected", "phone_number": phone_number}
		# Preserve the original connection time; only set it the first time.
		if not doc.connected_since:
			values["connected_since"] = frappe.utils.now()
		frappe.db.set_value("Whatsapp Instance", instance_name, values)
		frappe.db.commit()
	else:
		_store_status(instance_name, status, doc)

	return {"status": status, "phone_number": phone_number}


@frappe.whitelist()
def disconnect_instance(instance_name):
	"""Log the instance out via ``DELETE /instance/logout/{name}``."""
	doc = _get_instance(instance_name)
	doc.check_permission("write")
	base_url, api_key = _server_credentials(doc.evolution_server)

	_request("DELETE", base_url, f"/instance/logout/{instance_name}", api_key)

	return _store_status(instance_name, "Disconnected", doc)

@frappe.whitelist()
def get_user_instance_status():
    """Return WhatsApp instance status for the current logged-in user."""
    instance = frappe.db.get_value(
        "Whatsapp Instance",
        {"linked_user": frappe.session.user},
        ["name", "instance_name", "connection_status"],
        as_dict=True
    )
    
    if not instance:
        return None
    
    return {
        "instance_name": instance.instance_name or instance.name,
        "status": instance.connection_status or "Disconnected"
    }


