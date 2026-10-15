"""Encrypt legacy per-instance keys, without changing global server settings."""
import frappe
from frappe.utils.password import set_encrypted_password


def execute():
    for row in frappe.get_all("Whatsapp Instance", fields=["name", "api_key"]):
        raw = row.get("api_key")
        if raw and raw != "********":
            set_encrypted_password("Whatsapp Instance", row.name, raw, fieldname="api_key")
            frappe.db.set_value("Whatsapp Instance", row.name, "api_key", "********", update_modified=False)
