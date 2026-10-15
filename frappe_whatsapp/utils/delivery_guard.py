"""A durable send journal, independent of the surrounding SQL transaction.

All workers for a site MUST share its private filesystem. Never expire a claim:
an interrupted request may already have reached WhatsApp. Unknown outcomes need
provider reconciliation, not a blind retry. No credentials or payloads are stored.
"""

import hashlib
import json
import os
import time
from pathlib import Path

import frappe


class DeliveryUncertain(Exception):
    pass


class DeliveryRejected(Exception):
    """Provider explicitly rejected the request before accepting a send."""


REJECTED_HTTP_STATUSES = (400, 401, 403, 404, 405, 413, 415, 422, 429)


def delivery_key(*parts):
    return hashlib.sha256(json.dumps(parts, default=str, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def business_snapshot(value, ignored_fields=()):
    """Ignore save timestamps and the alert's own marker, not business changes."""
    ignored = {"creation", "modified", "modified_by", "owner", *ignored_fields}
    if isinstance(value, dict):
        return {
            key: business_snapshot(item, ignored_fields)
            for key, item in value.items()
            if key not in ignored and not key.startswith("_")
        }
    if isinstance(value, (list, tuple)):
        return [business_snapshot(item, ignored_fields) for item in value]
    return value


def _path(key):
    folder = Path(frappe.get_site_path("private", "whatsapp-delivery-journal"))
    folder.mkdir(parents=True, exist_ok=True)
    _sync_directory(folder.parent)
    return folder / (delivery_key(key) + ".json")


def _sync_directory(folder):
    # Linux deployment: persist directory entries as well as file contents.
    if os.name != "nt":
        fd = os.open(str(folder), os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _write_atomic(path, data):
    temporary = path.with_name(path.name + "." + frappe.generate_hash(length=16) + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            json.dump(data, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _sync_directory(path.parent)
    finally:
        if temporary.exists():
            temporary.unlink()


def send_once(key, send, fingerprint=None, replay_seconds=None):
    """Atomically claim an operation; replay its ID without another HTTP call."""
    path = _path(key)
    try:
        handle = path.open("x", encoding="utf-8")
    except FileExistsError:
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            state = {}
        if fingerprint and state.get("fingerprint") and state["fingerprint"] != fingerprint:
            raise ValueError("This operation key is already bound to a different WhatsApp payload.")
        if (replay_seconds and state.get("state") == "sent" and state.get("accepted_at")
                and time.time() - state["accepted_at"] >= replay_seconds):
            recycle = path.with_suffix(".recycle")
            try:
                recycle.mkdir()
            except FileExistsError:
                raise DeliveryUncertain("A new WhatsApp operation is already being reserved.")
            try:
                current = json.loads(path.read_text(encoding="utf-8"))
                if (current.get("state") == "sent" and current.get("accepted_at")
                        and time.time() - current["accepted_at"] >= replay_seconds):
                    path.unlink()
                    _sync_directory(path.parent)
            finally:
                recycle.rmdir()
            return send_once(key, send, fingerprint, replay_seconds)
        if state.get("state") == "sent" and state.get("message_id"):
            return state["message_id"]
        raise DeliveryUncertain(
            "WhatsApp operation is in progress or its delivery is uncertain. "
            "Check the provider before resending. Journal: " + path.name
        )

    # Persist the claim BEFORE the provider is called. SQL rollback cannot erase it.
    with handle:
        json.dump({"state": "pending", "fingerprint": fingerprint}, handle)
        handle.flush()
        os.fsync(handle.fileno())
    _sync_directory(path.parent)
    try:
        message_id = send()
        if not message_id:
            raise DeliveryUncertain("Provider returned no WhatsApp message ID.")
        _write_atomic(path, {"state": "sent", "message_id": message_id, "fingerprint": fingerprint, "accepted_at": time.time()})
        return message_id
    except DeliveryRejected:
        path.unlink()
        _sync_directory(path.parent)
        raise
    except Exception as exc:
        # Keep the claim even if recording the error fails or this worker dies.
        # A later invocation must never assume the provider did not accept it.
        raise DeliveryUncertain(
            "WhatsApp delivery is uncertain; verify with the provider before retrying. "
            "Journal: " + path.name + ". Cause: " + str(exc)
        ) from exc


def reconcile(journal_name, message_id=None, confirmed_not_sent=False):
    """Administrator/bench-only recovery, after checking provider + stopping jobs.

    Not whitelisted. Use the filename printed in the error. Back up the journal.
    Supply the accepted ID, OR explicitly confirm the provider did not send it.
    """
    if Path(journal_name).name != journal_name or not journal_name.endswith(".json"):
        raise ValueError("Expected a journal filename, not a path")
    if bool(message_id) == bool(confirmed_not_sent):
        raise ValueError("Supply a message ID OR confirmed_not_sent=True")
    path = Path(frappe.get_site_path("private", "whatsapp-delivery-journal", journal_name))
    if not path.exists():
        raise ValueError("Journal does not exist")
    if message_id:
        previous = json.loads(path.read_text(encoding="utf-8") or "{}")
        _write_atomic(path, {"state": "sent", "message_id": str(message_id), "fingerprint": previous.get("fingerprint")})
    else:
        path.unlink()
        _sync_directory(path.parent)
