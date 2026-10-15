"""Offline regression tests. No Frappe site, HTTP request or WhatsApp send needed.

Run: python -m unittest discover -s tests -p test_delivery_safety.py -v
"""

import importlib.util
import datetime
import json
from pathlib import Path
import sys
import tempfile
import threading
import types
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
site = tempfile.TemporaryDirectory()
frappe = types.ModuleType("frappe")
frappe.get_site_path = lambda *parts: str(Path(site.name).joinpath(*parts))
frappe.generate_hash = lambda length=16: uuid.uuid4().hex[:length]
frappe._ = lambda text: text
frappe.whitelist = lambda *args, **kwargs: lambda fn: fn
frappe.throw = lambda text, **kwargs: (_ for _ in ()).throw(RuntimeError(text))
sys.modules["frappe"] = frappe
utils_package = types.ModuleType("frappe_whatsapp.utils")
utils_package.__path__ = [str(ROOT / "frappe_whatsapp/utils")]
sys.modules[utils_package.__name__] = utils_package
requests = types.ModuleType("requests")
requests.post = lambda *a, **kw: (_ for _ in ()).throw(AssertionError("Unexpected HTTP call"))
sys.modules["requests"] = requests
frappe_utils = types.ModuleType("frappe.utils")
frappe_utils.cint = lambda value: int(value or 0)
frappe_utils.get_datetime = lambda value: datetime.datetime.fromisoformat(value) if isinstance(value, str) else value
frappe_utils.now_datetime = datetime.datetime.now
frappe_utils.nowdate = lambda: datetime.date.today().isoformat()
frappe_utils.now = lambda: datetime.datetime.now().isoformat()
frappe_utils.getdate = lambda value: datetime.date.fromisoformat(value)
frappe_utils.get_url = lambda: "https://example.invalid"
sys.modules["frappe.utils"] = frappe_utils


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


guard = load("frappe_whatsapp.utils.delivery_guard", "frappe_whatsapp/utils/delivery_guard.py")


class TestDeliveryJournal(unittest.TestCase):
    def setUp(self):
        self.key = uuid.uuid4().hex

    def test_business_key_survives_save_but_changes_for_changed_document(self):
        first = {"name": "INV-1", "modified": "old", "total": 10, "wa_sent": 0}
        second = {"name": "INV-1", "modified": "new", "total": 10, "wa_sent": 1}
        self.assertEqual(guard.business_snapshot(first, ("wa_sent",)), guard.business_snapshot(second, ("wa_sent",)))
        second["total"] = 20
        self.assertNotEqual(guard.business_snapshot(first, ("wa_sent",)), guard.business_snapshot(second, ("wa_sent",)))

    def test_success_replayed_after_database_rollback(self):
        calls = []
        self.assertEqual(guard.send_once(self.key, lambda: calls.append(1) or "id-1"), "id-1")
        # SQL state is deliberately irrelevant: the journal is outside SQL.
        self.assertEqual(guard.send_once(self.key, lambda: calls.append(2) or "id-2"), "id-1")
        self.assertEqual(calls, [1])

    def test_concurrent_workers_make_only_one_provider_call(self):
        entered, release = threading.Event(), threading.Event()
        calls = []

        def provider():
            calls.append(1)
            entered.set()
            if not release.wait(5):
                raise RuntimeError("test timed out")
            return "concurrent-id"

        with ThreadPoolExecutor(max_workers=8) as pool:
            first = pool.submit(guard.send_once, self.key, provider)
            self.assertTrue(entered.wait(5))
            others = [pool.submit(guard.send_once, self.key, provider) for _ in range(6)]
            try:
                for result in others:
                    with self.assertRaises(guard.DeliveryUncertain):
                        result.result(timeout=5)
            finally:
                release.set()
            self.assertEqual(first.result(timeout=5), "concurrent-id")
        self.assertEqual(calls, [1])

    def test_timeout_never_blindly_retried(self):
        calls = []

        def accepted_but_lost_response():
            calls.append(1)
            raise TimeoutError("response lost")

        with self.assertRaises(guard.DeliveryUncertain):
            guard.send_once(self.key, accepted_but_lost_response)
        with self.assertRaises(guard.DeliveryUncertain):
            guard.send_once(self.key, accepted_but_lost_response)
        self.assertEqual(calls, [1])

    def test_explicit_rejection_can_be_retried_after_correction(self):
        def rejected():
            raise guard.DeliveryRejected("invalid recipient")
        with self.assertRaises(guard.DeliveryRejected):
            guard.send_once(self.key, rejected)
        self.assertEqual(guard.send_once(self.key, lambda: "corrected-id"), "corrected-id")

    def test_missing_provider_id_blocks_retry(self):
        with self.assertRaises(guard.DeliveryUncertain):
            guard.send_once(self.key, lambda: "")
        with self.assertRaises(guard.DeliveryUncertain):
            guard.send_once(self.key, lambda: "new-id")

    def test_corrupt_or_interrupted_claim_fails_closed(self):
        guard._path(self.key).write_text("", encoding="utf-8")
        with self.assertRaises(guard.DeliveryUncertain):
            guard.send_once(self.key, lambda: self.fail("must not send"))

    def test_failed_success_journal_write_does_not_resend(self):
        with patch.object(guard, "_write_atomic", side_effect=OSError("disk full")):
            with self.assertRaises(guard.DeliveryUncertain):
                guard.send_once(self.key, lambda: "accepted-id")
        with self.assertRaises(guard.DeliveryUncertain):
            guard.send_once(self.key, lambda: self.fail("must not send"))

    def test_reconciliation_with_provider_id_recovers_without_send(self):
        guard._path(self.key).write_text('{"state":"pending"}', encoding="utf-8")
        guard.reconcile(guard._path(self.key).name, message_id="verified-id")
        self.assertEqual(guard.send_once(self.key, lambda: self.fail("must not send")), "verified-id")

    def test_explicit_confirmed_not_sent_allows_recovery(self):
        guard._path(self.key).write_text('{"state":"pending"}', encoding="utf-8")
        guard.reconcile(guard._path(self.key).name, confirmed_not_sent=True)
        self.assertEqual(guard.send_once(self.key, lambda: "first-real-send"), "first-real-send")

    def test_no_transport_when_journal_cannot_be_created(self):
        with patch.object(guard, "_path", side_effect=PermissionError("read only")):
            with self.assertRaises(PermissionError):
                guard.send_once(self.key, lambda: self.fail("must not send"))

    def test_different_operations_are_not_suppressed(self):
        self.assertEqual(guard.send_once(self.key, lambda: "one"), "one")
        self.assertEqual(guard.send_once(self.key + "2", lambda: "two"), "two")


# Load the real controller with small stand-ins for its framework imports.
integrations = types.ModuleType("frappe.integrations.utils")
integrations.make_post_request = lambda *a, **kw: None
sys.modules[integrations.__name__] = integrations
document = types.ModuleType("frappe.model.document")
document.Document = object
sys.modules[document.__name__] = document
messaging = types.ModuleType("frappe_whatsapp.utils.messaging")
messaging.ensure_enabled = lambda: None
messaging.get_default_channel = lambda: "Meta"
messaging.normalize_number = lambda value: "".join(c for c in str(value) if c.isdigit())
messaging.resolve_channel = lambda value=None: value or "Meta"
messaging.resolve_instance_name = lambda *a, **kw: "instance"
sys.modules[messaging.__name__] = messaging
controller = load("safety_message", "frappe_whatsapp/frappe_whatsapp/doctype/whatsapp_message/whatsapp_message.py")

api = types.ModuleType("frappe_whatsapp.api")
api._request = lambda *a, **kw: {"key": {"id": "evo-id"}}
api._server_credentials = lambda *a: ("https://example.invalid", "test-key")
sys.modules[api.__name__] = api
evolution = load("safety_evolution", "frappe_whatsapp/utils/evolution.py")
naming = types.ModuleType("frappe.model.naming")
naming.make_autoname = lambda value: "campaign"
sys.modules[naming.__name__] = naming
bulk = load("safety_bulk", "frappe_whatsapp/frappe_whatsapp/doctype/bulk_whatsapp_message/bulk_whatsapp_message.py")
report = load("safety_report", "frappe_whatsapp/frappe_whatsapp/doctype/whatsapp_scheduled_report/whatsapp_scheduled_report.py")
event_utils = types.ModuleType("frappe.core.doctype.server_script.server_script_utils")
event_utils.EVENT_MAP = {}
sys.modules[event_utils.__name__] = event_utils
legacy_schedules = load("safety_legacy_schedules", "frappe_whatsapp/utils/__init__.py")


class Flags(dict):
    __getattr__ = dict.get
    __setattr__ = dict.__setitem__


class Message(controller.WhatsAppMessage):
    def __init__(self, **data):
        self.flags = Flags()
        self.name = None
        self.to = "966501234567"
        self.message_id = None
        self.__dict__.update(data)

    def get(self, key):
        return getattr(self, key, None)

    def _meta_settings(self):
        return types.SimpleNamespace(url="https://example.invalid", version="v1", phone_id="1"), "test-token"


class TestRoutingSafety(unittest.TestCase):
    def test_legacy_calendar_schedules_do_not_send_again(self):
        # get_list does not exist in the stand-in; accessing it would fail.
        for frequency in ("Daily", "Weekly", "Monthly"):
            legacy_schedules.trigger_whatsapp_notifications(frequency)

    def test_report_already_sent_this_hour_is_not_due(self):
        doc = report.WhatsAppScheduledReport()
        doc.disabled = False
        doc.last_sent_on = "2026-10-15 08:03:00"
        doc.repeat_every = 1
        doc.schedule_time = "08:00:00"
        doc.frequency = "Daily"
        self.assertFalse(doc.is_due(datetime.date(2026, 10, 15), 8, "Thursday"))
        doc.last_sent_on = "2026-10-14 08:03:00"
        self.assertTrue(doc.is_due(datetime.date(2026, 10, 15), 8, "Thursday"))

    def test_bulk_recipient_numbers_are_deduplicated(self):
        doc = bulk.BulkWhatsAppMessage()
        doc._gather_recipients_raw = lambda: [
            {"mobile_number": "+966 50 123 4567"},
            {"mobile_number": "966501234567"},
            {"mobile_number": "966501234568"},
        ]
        self.assertEqual(len(doc._gather_recipients()), 2)

    def test_retry_rechecks_locked_status_and_preserves_provider_id(self):
        for status, existing_id in (("Sent", "id"), ("Failed", "accepted-id")):
            with self.subTest(status=status):
                msg = types.SimpleNamespace(status=status, message_id=existing_id)
                msg.reload = lambda: None
                msg._send_via_meta = lambda: self.fail("must not resend")
                doc = bulk.BulkWhatsAppMessage()
                doc.name = "campaign"
                doc._channel = "Meta"
                doc._sender_instance = None
                doc._log = lambda value: None
                doc._finish = lambda *args: None
                database = types.SimpleNamespace(
                    sql=lambda *a: None,
                    count=lambda *a: 0,
                    set_value=lambda *a: self.fail("must not overwrite ID"),
                )
                msg.to = "966501234567"
                with patch.object(frappe, "db", database, create=True), patch.object(
                    frappe, "get_all", return_value=["row"], create=True
                ), patch.object(frappe, "get_doc", return_value=msg, create=True):
                    doc._retry_failed_messages()
                self.assertEqual(msg.message_id, existing_id)

    def test_evolution_all_message_types_share_guard(self):
        calls = [
            lambda key: evolution.send_text("966501234567", "hello", instance_name="i", base_url="https://example.invalid", operation_key=key),
            lambda key: evolution.send_media("966501234567", "https://example.invalid/f.pdf", instance_name="i", base_url="https://example.invalid", operation_key=key),
            lambda key: evolution.send_buttons("966501234567", "t", "d", [], instance_name="i", base_url="https://example.invalid", operation_key=key),
            lambda key: evolution.send_list("966501234567", "t", "d", "open", [], instance_name="i", base_url="https://example.invalid", operation_key=key),
        ]
        for call in calls:
            with self.subTest(call=call):
                key = uuid.uuid4().hex
                with patch.object(evolution, "_request", return_value={"key": {"id": "evo-id"}}) as request:
                    self.assertEqual(call(key), "evo-id")
                    self.assertEqual(call(key), "evo-id")
                self.assertEqual(request.call_count, 1)

    def test_bulk_original_and_retry_share_operation(self):
        campaign = uuid.uuid4().hex
        original = Message(bulk_message_reference=campaign, name=None)
        retry = Message(bulk_message_reference=campaign, name="existing-failed-row")
        self.assertEqual(original.delivery_operation_key(), retry.delivery_operation_key())

    def test_new_messages_before_naming_have_distinct_operations(self):
        first, second = Message(), Message()
        self.assertNotEqual(first.delivery_operation_key(), second.delivery_operation_key())
        self.assertEqual(first.delivery_operation_key(), first.delivery_operation_key())

    def test_notification_operation_survives_new_row_name(self):
        first, second = Message(name="one"), Message(name="two")
        first.flags.delivery_scope = second.flags.delivery_scope = ("notification", uuid.uuid4().hex)
        self.assertEqual(first.delivery_operation_key(), second.delivery_operation_key())

    def test_meta_transport_replayed_without_second_http_request(self):
        msg = Message()
        with patch.object(controller, "make_post_request", return_value={"messages": [{"id": "meta-id"}]}) as post:
            msg.notify({"type": "text", "to": msg.to})
            msg.notify({"type": "text", "to": msg.to})
        self.assertEqual(msg.message_id, "meta-id")
        self.assertEqual(post.call_count, 1)


if __name__ == "__main__":
    unittest.main()
