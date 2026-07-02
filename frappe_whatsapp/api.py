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


def _server_credentials(server_name):
	"""Return ``(base_url, api_key)`` for the given Evolution Server.

	Credentials are decrypted from the Evolution Server doctype so they are
	never exposed in code or to the client.
	"""
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


def _get_instance(instance_name):
	"""Load a Whatsapp Instance document with a read permission check."""
	if not frappe.db.exists("Whatsapp Instance", instance_name):
		frappe.throw(_("WhatsApp instance {0} was not found.").format(frappe.bold(instance_name)))

	doc = frappe.get_doc("Whatsapp Instance", instance_name)
	doc.check_permission("read")
	return doc


def _apply_status(doc, state):
	"""Translate an Evolution state into our status and persist any change."""
	status = STATE_MAP.get((state or "").lower(), "Disconnected")

	updates = {}
	if doc.connection_status != status:
		updates["connection_status"] = status

	if status == "Connected" and not doc.connected_since:
		updates["connected_since"] = frappe.utils.now_datetime()
	elif status == "Disconnected" and doc.connected_since:
		updates["connected_since"] = None

	if updates:
		doc.db_set(updates, notify=True, commit=True)

	return status


@frappe.whitelist()
def check_user_instance(user):
	"""Return the name of the existing instance for ``user``, if any."""
	if not user:
		return None

	return frappe.db.get_value("Whatsapp Instance", {"linked_user": user}, "name")


@frappe.whitelist()
def get_qr_code(instance_name):
	"""Fetch a QR code / pairing payload from ``GET /instance/connect/{name}``.

	Returns a dict containing (when available) ``base64``, ``code`` and the
	current ``status``.
	"""
	doc = _get_instance(instance_name)
	base_url, api_key = _server_credentials(doc.evolution_server)

	data = _request("GET", base_url, f"/instance/connect/{instance_name}", api_key)

	# When the instance is already connected the API returns the state instead
	# of a QR payload.
	state = (data.get("instance") or {}).get("state")
	if state:
		status = _apply_status(doc, state)
	else:
		status = _apply_status(doc, "connecting")

	return {
		"base64": data.get("base64"),
		"code": data.get("code") or data.get("pairingCode"),
		"status": status,
	}


@frappe.whitelist()
def get_instance_status(instance_name):
	"""Fetch state from ``GET /instance/connectionState/{name}``.

	Updates ``connection_status`` in the database and returns the resulting
	status string (Connected / Connecting / Disconnected).
	"""
	doc = _get_instance(instance_name)
	base_url, api_key = _server_credentials(doc.evolution_server)

	data = _request("GET", base_url, f"/instance/connectionState/{instance_name}", api_key)
	state = (data.get("instance") or {}).get("state") or data.get("state")

	return _apply_status(doc, state)


@frappe.whitelist()
def disconnect_instance(instance_name):
	"""Log the instance out via ``DELETE /instance/logout/{name}``."""
	doc = _get_instance(instance_name)
	doc.check_permission("write")
	base_url, api_key = _server_credentials(doc.evolution_server)

	_request("DELETE", base_url, f"/instance/logout/{instance_name}", api_key)

	_apply_status(doc, "close")
	return "Disconnected"


@frappe.whitelist()
def create_instance(evolution_server, linked_user, phone_number=None):
	"""Create a new WhatsApp instance on the Evolution API and store it.

	Steps:
	  1. Create the Whatsapp Instance doc so the instance name is generated
	     (site prefix + 5 random digits) and the one-instance-per-user rule is
	     enforced.
	  2. Call ``POST /instance/create`` on the Evolution API using that name.
	  3. Persist the per-instance API key returned by the Evolution API.

	Returns the saved Whatsapp Instance document.
	"""
	if not frappe.has_permission("Whatsapp Instance", "create"):
		frappe.throw(_("You are not permitted to create WhatsApp instances."))

	doc = frappe.get_doc(
		{
			"doctype": "Whatsapp Instance",
			"evolution_server": evolution_server,
			"linked_user": linked_user,
			"phone_number": phone_number,
			"connection_status": "Connecting",
		}
	)
	doc.insert()

	base_url, api_key = _server_credentials(evolution_server)
	payload = {
		"instanceName": doc.instance_name,
		"integration": "WHATSAPP-BAILEYS",
		"qrcode": True,
	}
	if phone_number:
		payload["number"] = phone_number

	data = _request("POST", base_url, "/instance/create", api_key, payload=payload)

	# The Evolution API returns the per-instance key under "hash" (v2) or
	# nested under "hash.apikey" (v1).
	instance_key = None
	hash_value = data.get("hash")
	if isinstance(hash_value, dict):
		instance_key = hash_value.get("apikey")
	elif isinstance(hash_value, str):
		instance_key = hash_value

	if instance_key:
		doc.db_set("api_key", instance_key)

	doc.reload()
	return doc
