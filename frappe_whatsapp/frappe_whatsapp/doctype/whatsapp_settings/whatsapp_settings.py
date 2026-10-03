# Copyright (c) 2022, Shridhar Patil and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class WhatsAppSettings(Document):
    def validate(self):
        if self.default_channel not in ("Meta", "Evolution"):
            frappe.throw(_("Default Channel must be Meta or Evolution."))

        if self.default_channel == "Evolution" and self.default_evolution_instance:
            if not frappe.db.exists("Whatsapp Instance", self.default_evolution_instance):
                frappe.throw(_("Default Evolution Instance does not exist."))

        if self.retention_days is not None and int(self.retention_days or 0) < 0:
            frappe.throw(_("Retention Days cannot be negative."))
        if self.max_attachment_size is not None and int(self.max_attachment_size or 0) <= 0:
            frappe.throw(_("Max Attachment Size must be greater than zero."))

    def on_update(self):
        frappe.clear_cache(doctype="WhatsApp Settings")
