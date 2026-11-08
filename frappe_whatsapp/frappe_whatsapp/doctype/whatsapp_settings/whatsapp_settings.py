# Copyright (c) 2022, Shridhar Patil and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class WhatsAppSettings(Document):
    def validate(self):
        self.default_channel = self.default_channel or "Evolution"
        if self.default_channel not in ("Meta", "Evolution"):
            frappe.throw(_("Default Channel must be Meta or Evolution."))

        if self.retention_days is not None and int(self.retention_days or 0) < 0:
            frappe.throw(_("Retention Days cannot be negative."))
        if self.max_attachment_size is None or int(self.max_attachment_size or 0) <= 0:
            self.max_attachment_size = 10

    def on_update(self):
        frappe.clear_cache(doctype="WhatsApp Settings")
