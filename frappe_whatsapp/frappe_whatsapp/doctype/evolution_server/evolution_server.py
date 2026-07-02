# Copyright (c) 2026, Shridhar Patil and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


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
		"""Return the global API key."""
		return self.api_key or ""
