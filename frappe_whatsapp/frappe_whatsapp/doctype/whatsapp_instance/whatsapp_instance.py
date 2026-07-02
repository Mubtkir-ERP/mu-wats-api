# Copyright (c) 2026, Shridhar Patil and contributors
# For license information, please see license.txt

import random

import frappe
from frappe import _
from frappe.model.document import Document


class WhatsappInstance(Document):
	"""A single WhatsApp connection hosted on an Evolution API server."""

	def autoname(self):
		"""Generate the instance name as: <site prefix>-<5 random digits>.

		Example: samad-48291. The site prefix is the part of the site name
		before the first dot. Retries until a unique value is found so two
		instances never collide.
		"""
		if not self.instance_name:
			self.instance_name = self.generate_instance_name()

	def generate_instance_name(self):
		"""Build a unique instance name for the current site."""
		site = getattr(frappe.local, "site", "") or ""
		prefix = site.split(".")[0] or "wa"

		# Try a bounded number of times before giving up.
		for _attempt in range(20):
			digits = "".join(random.choices("0123456789", k=5))
			candidate = f"{prefix}-{digits}"
			if not frappe.db.exists("Whatsapp Instance", candidate):
				return candidate

		frappe.throw(
			_("Could not generate a unique instance name. Please try again."),
			title=_("Instance Name Error"),
		)

	def validate(self):
		"""Run all validations."""
		self.validate_single_instance_per_user()

	def validate_single_instance_per_user(self):
		"""Block creating more than one instance for the same linked user."""
		if not self.linked_user:
			return

		existing = frappe.db.get_value(
			"Whatsapp Instance",
			{
				"linked_user": self.linked_user,
				"name": ["!=", self.name or ""],
			},
			"name",
		)

		if existing:
			frappe.throw(
				_(
					"User {0} already has a WhatsApp instance ({1}). "
					"Only one instance is allowed per user."
				).format(frappe.bold(self.linked_user), frappe.bold(existing)),
				title=_("Duplicate Instance"),
			)
