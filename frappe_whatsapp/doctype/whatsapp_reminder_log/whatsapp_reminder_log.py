# Copyright (c) 2026, Shridhar Patil and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class WhatsAppReminderLog(Document):
	"""Per-document state for recurring WhatsApp reminders.

	One record per (notification, reference document). Tracks the last reminder
	date, how many were sent, whether the trigger condition has cleared (e.g. the
	invoice was paid) and whether a thank-you message was already sent.
	"""

	pass
