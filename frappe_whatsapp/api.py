# Copyright (c) 2026, Shridhar Patil and contributors
# For license information, please see license.txt
"""Whitelisted API for WhatsApp instance management via the Evolution API.

The Evolution API base URL and API key are always resolved from the
``Evolution Server`` doctype and are never hardcoded here.
"""

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
	base_url = server.get_base_url()
	api_key = server.get_api_key()

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

	Raises a translated, user-facing error on failure.
	"""
	url = f"{base_url}{path}"
	try:
		response = requests.request(
			method,
			url,
			headers=_headers(api_key),
			json=payload,
			timeout=REQUEST_TIMEOUT,
		)
		response.raise_for_status()
		if response.content:
			return response.json()
		return {}
	except requests.exceptions.RequestException as exc:
		frappe.log_error(
			message=f"{method} {url}\n{exc}",
			title="Evolution API Request Failed",
		)
		frappe.throw(
			_("Could not reach the Evolution API server. Please try again later."),
			title=_("Connection Error"),
		)


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


def provision_instance(doc):
	"""Create the instance on the Evolution API and store its key + status.

	Called from ``Whatsapp Instance.after_insert``. Sends
	``POST /instance/create`` with the WHATSAPP-BAILEYS integration, then
	persists the returned per-instance API key and connection status.
	"""
	base_url, api_key = _server_credentials(doc.evolution_server)

	payload = {
		"instanceName": doc.instance_name,
		"integration": "WHATSAPP-BAILEYS",
	}
	data = _request("POST", base_url, "/instance/create", api_key, payload=payload)

	instance_key = _extract_api_key(data)
	if instance_key:
		doc.db_set("api_key", instance_key)

	# A freshly created instance is awaiting a QR scan; default to Connecting
	# when the API does not report a recognised state.
	instance = data.get("instance") or {}
	raw_state = instance.get("state") or instance.get("status") or data.get("status")
	status = map_state(raw_state) if raw_state else "Connecting"
	if status == "Disconnected":
		status = "Connecting"

	doc.db_set("connection_status", status)
	return status


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


@frappe.whitelist()
def create_instance(evolution_server, linked_user=None, phone_number=None):
	"""Create a Whatsapp Instance document.

	The Evolution API instance itself is provisioned automatically by the
	doctype's ``after_insert`` hook (see ``provision_instance``), which also
	stores the per-instance API key and status. Returns the saved document.
	"""
	if not frappe.has_permission("Whatsapp Instance", "create"):
		frappe.throw(_("You are not permitted to create WhatsApp instances."))

	doc = frappe.get_doc(
		{
			"doctype": "Whatsapp Instance",
			"evolution_server": evolution_server,
			"linked_user": linked_user,
			"phone_number": phone_number,
		}
	)
	doc.insert()
	doc.reload()
	return doc
