# Copyright (c) 2026, Shridhar Patil and contributors
# For license information, please see license.txt
"""Whitelisted API for WhatsApp instance management via the Evolution API.

The Evolution API base URL and API key are always resolved from the
``Evolution Server`` doctype and are never hardcoded here.
"""

import traceback

import requests

import frappe
from frappe import _

# Timeout (in seconds) for every outbound Evolution API request.
REQUEST_TIMEOUT = 30

# Mapping of Evolution API connection states to our stored status values.
STATE_MAP = {
	"open": "Connected",
	"connecting": "Connecting",
	"close": "Disconnected",
	"closed": "Disconnected",
}


def map_state(state):
	"""Map an Evolution API state to our status.

	``open`` -> Connected, ``connecting`` -> Connecting, anything else ->
	Disconnected.
	"""
	return STATE_MAP.get((state or "").lower(), "Disconnected")


def _server_credentials(server_name):
	"""Return ``(base_url, api_key)`` for the given Evolution Server."""
	if not server_name:
		frappe.throw(_("No Evolution Server is configured for this instance."))

	server = frappe.get_doc("Evolution Server", server_name)
	# Normalise the base URL: strip any trailing slash so paths join cleanly.
	# api_key is a Password field, so read it via get_password (not the attribute).
	base_url = server.get_base_url()
	api_key = server.get_api_key()

	# Log which server / base_url is actually being used. Only the first 20
	# characters of the URL are logged, for security.
	frappe.logger().error(
		f"Evolution Server in use: {server_name} | base_url={base_url[:20]!r} | "
		f"api_key_set={bool(api_key)}"
	)

	if not base_url or not api_key:
		frappe.throw(
			_("Evolution Server {0} is missing its Base URL or API Key.").format(
				frappe.bold(server_name)
			)
		)

	return base_url, api_key


def _headers(api_key):
	"""Build the standard Evolution API request headers."""
	return {"apikey": api_key, "Content-Type": "application/json"}


def _request(method, base_url, path, api_key, payload=None):
	"""Perform an Evolution API request and return the parsed JSON body.

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
		frappe.logger().error(f"Evolution API connection error [{method} {url}]:\n{tb}")
		frappe.log_error(message=f"{method} {url}\n{tb}", title="Evolution API Connection Error")
		frappe.throw(
			_("Evolution API error: {0}").format(str(exc)),
			title=_("Connection Error"),
		)

	# --- Always log the raw response so we can see exactly what came back. ---
	frappe.logger().error(
		f"Evolution API response [{method} {url}]: {response.status_code} — {response.text}"
	)

	# --- HTTP layer: 4xx / 5xx. Surface the status + body, don't hide it. ---
	if response.status_code >= 400:
		frappe.log_error(
			message=f"{method} {url}\n{response.status_code} {response.text}",
			title="Evolution API HTTP Error",
		)
		frappe.throw(
			_("Evolution API error: {0} — {1}").format(
				response.status_code, (response.text or "")[:500]
			),
			title=_("Evolution API Error"),
		)

	if response.content:
		try:
			return response.json()
		except ValueError:
			return {}
	return {}


def _extract_api_key(data):
	"""Extract the per-instance API key from a create response.

	The Evolution API returns the key under ``hash`` (v2, a string) or under
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


def _generate_instance_name():
	"""Return a unique ``<site prefix (<=10)>-<5 digits>`` instance name."""
	import string

	site = getattr(frappe.local, "site", "") or ""
	site_prefix = (site.split(".")[0] or "wa")[:10]

	for _attempt in range(20):
		suffix = "".join(random.choices(string.digits, k=5))
		candidate = f"{site_prefix}-{suffix}"
		if not frappe.db.exists("Whatsapp Instance", candidate):
			return candidate

	frappe.throw(
		_("Could not generate a unique instance name. Please try again."),
		title=_("Instance Name Error"),
	)


@frappe.whitelist()
def create_whatsapp_instance(evolution_server, linked_user, phone_number=None):
	"""Create a WhatsApp instance on the Evolution API, then store it locally.

	The record is only inserted into ERPNext after the Evolution API confirms
	creation. Returns ``{"instance_name", "api_key"}`` on success.
	"""
	if not frappe.has_permission("Whatsapp Instance", "create"):
		frappe.throw(_("You are not permitted to create WhatsApp instances."))

	# 1. Block a second instance for the same user.
	existing = frappe.db.get_value("Whatsapp Instance", {"linked_user": linked_user}, "name")
	if existing:
		frappe.throw(
			_("User already has an instance: {0}").format(existing),
			title=_("Duplicate Instance"),
		)

	# 2. Generate the instance name (site prefix + 5 random digits).
	server = frappe.get_doc("Evolution Server", evolution_server)
	instance_name = _generate_instance_name()

	# 3. Call the Evolution API to create the instance.
	base_url = _normalise_base_url(server.base_url)
	server_api_key = server.get_password("api_key", raise_exception=False)
	url = f"{base_url}/instance/create"

	frappe.logger().error(
		f"Creating Evolution instance '{instance_name}' on {base_url[:20]!r}"
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
			f"Evolution API connection error [POST {url}]:\n{traceback.format_exc()}"
		)
		frappe.throw(
			_("Evolution API error: {0}").format(str(exc)),
			title=_("Connection Error"),
		)

	frappe.logger().error(
		f"Evolution API response [POST {url}]: {response.status_code} — {response.text}"
	)
	if response.status_code not in (200, 201):
		frappe.throw(
			_("Evolution API error: {0} — {1}").format(
				response.status_code, (response.text or "")[:500]
			),
			title=_("Evolution API Error"),
		)

	data = response.json() if response.content else {}
	instance_key = (
		_extract_api_key(data) or data.get("apikey") or ""
	)

	# 4. Persist the confirmed instance in ERPNext.
	doc = frappe.get_doc(
		{
			"doctype": "Whatsapp Instance",
			"instance_name": instance_name,
			"evolution_server": evolution_server,
			"linked_user": linked_user,
			"phone_number": phone_number,
			"api_key": instance_key,
			"connection_status": "Disconnected",
		}
	)
	doc.insert(ignore_permissions=True)
	frappe.db.commit()

	return {"instance_name": instance_name, "api_key": instance_key}


@frappe.whitelist()
def delete_whatsapp_instance(instance_name, delete_remote=False):
	"""Cleanup helper: remove a WhatsApp instance record (and optionally remote).

	Useful for removing stale/half-created instances (e.g. ``develop2-37757``).
	When ``delete_remote`` is truthy, also calls ``DELETE /instance/delete/{name}``
	on the Evolution API first. Missing local records are treated as already
	cleaned up.
	"""
	if not frappe.has_permission("Whatsapp Instance", "delete"):
		frappe.throw(_("You are not permitted to delete WhatsApp instances."))

	if not frappe.db.exists("Whatsapp Instance", instance_name):
		return {"deleted": False, "message": f"No local record named {instance_name}."}

	if frappe.utils.cint(delete_remote):
		doc = frappe.get_doc("Whatsapp Instance", instance_name)
		try:
			base_url, api_key = _server_credentials(doc.evolution_server)
			_request("DELETE", base_url, f"/instance/delete/{instance_name}", api_key)
		except Exception:
			# Remote may already be gone; log but still remove the local record.
			frappe.log_error(
				message=frappe.get_traceback(),
				title=f"Remote delete failed for {instance_name}",
			)

	frappe.delete_doc("Whatsapp Instance", instance_name, ignore_permissions=True)
	frappe.db.commit()
	return {"deleted": True, "message": f"Deleted {instance_name}."}


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

	Maps the state, updates ``connection_status`` in the DB via
	``frappe.db.set_value`` and returns the mapped status string
	(Connected / Connecting / Disconnected).
	"""
	doc = _get_instance(instance_name)
	base_url, api_key = _server_credentials(doc.evolution_server)

	data = _request("GET", base_url, f"/instance/connectionState/{instance_name}", api_key)
	state = (data.get("instance") or {}).get("state") or data.get("state")

	return _store_status(instance_name, map_state(state), doc)


@frappe.whitelist()
def disconnect_instance(instance_name):
	"""Log the instance out via ``DELETE /instance/logout/{name}``."""
	doc = _get_instance(instance_name)
	doc.check_permission("write")
	base_url, api_key = _server_credentials(doc.evolution_server)

	_request("DELETE", base_url, f"/instance/logout/{instance_name}", api_key)

	return _store_status(instance_name, "Disconnected", doc)


