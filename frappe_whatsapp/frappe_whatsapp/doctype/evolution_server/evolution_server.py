# Copyright (c) 2026, Shridhar Patil and contributors
# For license information, please see license.txt

import traceback

import requests

import frappe
from frappe import _
from frappe.model.document import Document

# Timeout (in seconds) for the connectivity test request.
TEST_TIMEOUT = 20


class EvolutionServer(Document):
	"""An Evolution API server used to host WhatsApp instances."""

	def validate(self):
		"""Normalise the base URL by stripping any trailing slash."""
		if self.base_url:
			self.base_url = self.base_url.rstrip("/")

	def get_base_url(self):
		"""Return the base URL without a trailing slash."""
		return (self.base_url or "").rstrip("/")

	def get_api_key(self):
		"""Return the decrypted global API key.

		``api_key`` is a Password field, so it must be read via get_password
		rather than accessing the attribute (which returns a masked value).
		"""
		return self.get_password("api_key", raise_exception=False) or ""

	@frappe.whitelist()
	def test_connection(self):
		"""Ping GET {base_url}/instance/fetchInstances and return the raw result.

		This intentionally does NOT raise on HTTP errors: it returns the raw
		status code and body so the exact server response can be inspected in
		the dialog. Credentials are read straight from this record.
		"""
		base_url = self.get_base_url()
		api_key = self.get_api_key()

		# Log which base_url is being tested (first 20 chars only, for security).
		frappe.logger().error(
			f"Testing Evolution Server '{self.name}' | base_url={base_url[:20]!r} | "
			f"api_key_set={bool(api_key)}"
		)

		if not base_url or not api_key:
			return {
				"ok": False,
				"url": base_url,
				"status_code": None,
				"body": "Base URL or API Key is not set on this Evolution Server.",
			}

		url = f"{base_url}/instance/fetchInstances"
		headers = {"apikey": api_key, "Content-Type": "application/json"}

		try:
			response = requests.get(url, headers=headers, timeout=TEST_TIMEOUT)
		except Exception as exc:
			tb = traceback.format_exc()
			frappe.logger().error(f"Evolution API connection error [GET {url}]:\n{tb}")
			return {
				"ok": False,
				"url": url,
				"status_code": None,
				"body": f"Connection error: {exc}",
			}

		frappe.logger().error(
			f"Evolution API response [GET {url}]: {response.status_code} — {response.text}"
		)

		return {
			"ok": response.status_code < 400,
			"url": url,
			"status_code": response.status_code,
			"body": response.text,
		}
