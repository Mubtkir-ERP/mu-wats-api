# Copyright (c) 2026, Shridhar Patil and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class EvolutionServer(Document):
	"""An Evolution API server used to host WhatsApp instances."""

	def validate(self):
		"""Normalise the base URL by stripping any trailing slash."""
		base_url = self.get_password("base_url", raise_exception=False)
		if base_url:
			normalised = base_url.rstrip("/")
			if normalised != base_url:
				self.base_url = normalised

	def get_base_url(self):
		"""Return the decrypted base URL without a trailing slash."""
		base_url = self.get_password("base_url", raise_exception=False)
		return (base_url or "").rstrip("/")

	def get_api_key(self):
		"""Return the decrypted global API key."""
		return self.get_password("api_key", raise_exception=False)
