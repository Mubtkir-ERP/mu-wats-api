"""SQL-transactional event claims and locks (shared by every site worker)."""

import hashlib
import uuid

import frappe


def _name(kind, namespace, identity):
    return hashlib.sha256(f"{kind}\0{namespace}\0{identity}".encode()).hexdigest()


def claim_event(namespace, identity, kind="Receipt"):
    name = _name(kind, namespace, identity)
    point = "wa_claim_" + uuid.uuid4().hex
    frappe.db.savepoint(point)
    try:
        frappe.get_doc({
            "doctype": "WhatsApp Operation", "kind": kind,
            "namespace": namespace[:140],
        }).insert(ignore_permissions=True, set_name=name)
    except frappe.DuplicateEntryError:
        frappe.db.rollback(save_point=point)
        return False
    return True


def transaction_lock(namespace, identity):
    claim_event(namespace, identity, kind="Lock")
    frappe.db.sql(
        "SELECT name FROM `tabWhatsApp Operation` WHERE name=%s FOR UPDATE",
        (_name("Lock", namespace, identity),),
    )


def run_transaction_once(namespace, identity, action):
    point = "wa_once_" + uuid.uuid4().hex
    frappe.db.savepoint(point)
    try:
        if claim_event(namespace, identity):
            return action()
    except Exception:
        frappe.db.rollback(save_point=point)
        raise


def create_once(namespace, identity, action, fingerprint=None):
    """Reuse the result of a repeated SQL-only create request."""
    point = "wa_create_" + uuid.uuid4().hex
    frappe.db.savepoint(point)
    try:
        if claim_event(namespace, identity):
            frappe.db.set_value("WhatsApp Operation", _name("Receipt", namespace, identity), "request_fingerprint", fingerprint)
            result = action()
            frappe.db.set_value("WhatsApp Operation", _name("Receipt", namespace, identity), "result_name", result["name"])
            return result
        previous = frappe.db.get_value("WhatsApp Operation", _name("Receipt", namespace, identity), "request_fingerprint")
        if previous != fingerprint:
            frappe.throw("This operation key was already used with different settings.")
        name = frappe.db.get_value("WhatsApp Operation", _name("Receipt", namespace, identity), "result_name")
        if not name:
            frappe.throw("The original operation has not completed. Try again later.")
        return {"name": name}
    except Exception:
        frappe.db.rollback(save_point=point)
        raise
