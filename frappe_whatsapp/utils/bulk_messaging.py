"""Whitelisted compatibility helpers for Bulk WhatsApp Message."""

import json

import frappe


@frappe.whitelist()
def get_progress(name):
    return frappe.get_doc("Bulk WhatsApp Message", name).get_progress()


@frappe.whitelist()
def retry_failed(name):
    return frappe.get_doc("Bulk WhatsApp Message", name).retry_failed()


@frappe.whitelist()
def import_recipients(list_name, doctype, mobile_field, name_field=None, filters=None, limit=None, data_fields=None):
    if filters and isinstance(filters, str):
        filters = json.loads(filters)
    if data_fields and isinstance(data_fields, str):
        data_fields = json.loads(data_fields)

    doc = frappe.get_doc("WhatsApp Recipient List", list_name)
    count = doc.import_list_from_doctype(doctype, mobile_field, name_field, filters, limit, data_fields)
    doc.save()
    return count


def schedule_bulk_messages():
    """Backward-compatible scheduler entry point."""
    from frappe_whatsapp.frappe_whatsapp.doctype.bulk_whatsapp_message.bulk_whatsapp_message import (
        trigger_scheduled_bulk_messages,
    )

    return trigger_scheduled_bulk_messages()
