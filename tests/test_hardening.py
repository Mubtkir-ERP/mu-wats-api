"""Offline security and compatibility regressions. Run this file separately."""
import datetime
import hashlib
import hmac
import importlib.util
import json
from pathlib import Path
import sys
import types
import unittest
import uuid
from unittest.mock import Mock, patch

import test_delivery_safety as base

ROOT = Path(__file__).resolve().parents[1]
package = types.ModuleType("frappe_whatsapp")
package.__path__ = [str(ROOT / "frappe_whatsapp")]
sys.modules["frappe_whatsapp"] = package
f = base.frappe
f._dict = base.Flags
f.PermissionError = PermissionError
f.ValidationError = ValueError
def throw(text, exc=RuntimeError, **kwargs):
    raise exc(text)
f.throw = throw
f.DuplicateEntryError = type("DuplicateEntryError", (Exception,), {})
f.utils = sys.modules["frappe.utils"]
f.utils.add_to_date = lambda value, **kw: value
f.utils.datetime = datetime
f.flags = base.Flags()
f.get_roles = lambda *a: []
f.session = base.Flags(user="operator")
f.local = types.SimpleNamespace(conf={"encryption_key": "DUMMY-SITE-SECRET"})
f.has_permission = Mock(return_value=True)
f.get_request_header = lambda name: ""

class Response:
    def __init__(self, content="", status=200, **kw):
        self.data = content
        self.status_code = status
        self.headers = {}

werkzeug = types.ModuleType("werkzeug.wrappers")
werkzeug.Response = Response
sys.modules["werkzeug.wrappers"] = werkzeug
safe_exec = types.ModuleType("frappe.utils.safe_exec")
safe_exec.get_safe_globals = lambda: {}
safe_exec.safe_exec = lambda *a, **kw: None
sys.modules[safe_exec.__name__] = safe_exec

def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module

security = load("frappe_whatsapp.utils.security", "frappe_whatsapp/utils/security.py")
media = load("frappe_whatsapp.utils.secure_media", "frappe_whatsapp/utils/secure_media.py")
operations = load("frappe_whatsapp.utils.operations", "frappe_whatsapp/utils/operations.py")
auth = load("frappe_whatsapp.utils.webhook_auth", "frappe_whatsapp/utils/webhook_auth.py")
webhook = load("security_webhook", "frappe_whatsapp/utils/webhook.py")
notification = load("security_notification", "frappe_whatsapp/frappe_whatsapp/doctype/whatsapp_notification/whatsapp_notification.py")
api = load("security_api", "frappe_whatsapp/api.py")
real_messaging = load("security_messaging", "frappe_whatsapp/utils/messaging.py")
sys.modules["frappe_whatsapp.utils.messaging"].can_advance_status = real_messaging.can_advance_status
sys.modules["frappe_whatsapp.utils.messaging"].normalise_message_status = real_messaging.normalise_message_status

class FakeDoc:
    def __init__(self, **values):
        self.__dict__.update(values)
    def __getattr__(self, key): return None
    def get(self, key): return getattr(self, key, None)

def settings(**values):
    doc = FakeDoc(phone_id="phone", url="https://graph.example.invalid", version="v1", max_attachment_size=16, **values)
    doc.get_password = lambda key, **kw: getattr(doc, key, "") or ""
    return doc

class TestAuthorization(unittest.TestCase):
    def setUp(self):
        f.session = base.Flags(user="operator")
        f.get_roles = lambda *a: []
        f.has_permission = Mock(return_value=True)
        sys.modules["frappe_whatsapp.utils.messaging"].resolve_instance_name = lambda name=None, user=None: name or "shared"

    def test_unauthorized_send_rejected_before_constructing_message(self):
        f.has_permission.side_effect = PermissionError("denied")
        f.get_doc = Mock()
        with self.assertRaises(PermissionError): api.send_text(number="966500000000", text="dummy")
        f.get_doc.assert_not_called()

    def test_guest_cannot_send_even_if_role_checker_were_permissive(self):
        f.session.user = "Guest"
        with self.assertRaises(Exception): security.require_sender()

    def test_app_manager_remains_authorized_without_global_message_read_grant(self):
        f.get_roles = lambda *a: ["WhatsApp Manager"]
        f.has_permission = Mock(side_effect=PermissionError("no global grant"))
        security.require_sender()
        f.has_permission.assert_not_called()

    def test_thirty_users_can_share_one_unlinked_instance(self):
        f.get_doc = lambda *a: FakeDoc(linked_user=None)
        for i in range(30):
            f.session.user = f"operator-{i}"
            self.assertEqual(security.authorize_instance("shared"), "shared")

    def test_other_users_linked_instance_requires_delegation(self):
        f.get_doc = lambda *a: FakeDoc(linked_user="other")
        f.has_permission.return_value = False
        with self.assertRaises(Exception): security.authorize_instance("private-number")
        f.has_permission.return_value = True
        self.assertEqual(security.authorize_instance("private-number"), "private-number")

    def test_owned_instance_does_not_require_server_secret_read_permission(self):
        f.get_doc = lambda *a: FakeDoc(linked_user="operator")
        f.has_permission.return_value = False
        self.assertEqual(security.authorize_instance("own"), "own")

    def test_automation_context_is_restored_after_failure(self):
        f.flags = base.Flags()
        with self.assertRaises(RuntimeError):
            with security.configured_automation():
                self.assertTrue(f.flags.whatsapp_configured_automation)
                raise RuntimeError("test")
        self.assertFalse(f.flags.whatsapp_configured_automation)

    def test_preview_checks_notification_permission_before_loading_reference(self):
        denied = FakeDoc(check_permission=Mock(side_effect=PermissionError("denied")))
        f.get_doc = Mock(return_value=denied)
        with self.assertRaises(PermissionError): notification.get_preview("restricted")
        self.assertEqual(f.get_doc.call_count, 1)

    def test_bulk_compatibility_wrapper_checks_document_write(self):
        helpers = load("security_bulk_helpers", "frappe_whatsapp/utils/bulk_messaging.py")
        retry = Mock()
        f.get_doc = lambda *a: FakeDoc(check_permission=Mock(side_effect=PermissionError("denied")), retry_failed=retry)
        with self.assertRaises(PermissionError): helpers.retry_failed("other-campaign")
        retry.assert_not_called()

class TestWebhookAuth(unittest.TestCase):
    def setUp(self):
        self.config = settings(meta_app_secret="META-SECRET", evolution_webhook_secret="")
        self.headers = {}
        self.data = {"instance": "shared", "event": "messages.upsert", "data": {}}
        self.raw = json.dumps(self.data).encode()
        f.get_request_header = lambda name: self.headers.get(name, "")
        f.request = types.SimpleNamespace(get_data=lambda: self.raw, get_json=lambda **kw: self.data)
        f.form_dict = {}
        f.db = types.SimpleNamespace(exists=lambda *a: True)
        f.get_doc = lambda dt, *a: self.config if dt == "WhatsApp Settings" else (FakeDoc(evolution_server="server") if dt == "Whatsapp Instance" else FakeDoc(get_api_key=lambda: "SERVER-KEY"))
        f.get_cached_doc = lambda *a: self.config

    def test_meta_valid_signature_preserves_exact_raw_body(self):
        self.headers["X-Hub-Signature-256"] = "sha256=" + hmac.new(b"META-SECRET", self.raw, hashlib.sha256).hexdigest()
        self.assertEqual(auth.meta_payload(), self.data)

    def test_meta_missing_signature_rejected_before_json_parse(self):
        f.request.get_json = Mock(side_effect=AssertionError("must not parse"))
        with self.assertRaises(Exception): auth.meta_payload()
        f.request.get_json.assert_not_called()

    def test_meta_tampered_body_is_rejected(self):
        self.headers["X-Hub-Signature-256"] = "sha256=" + hmac.new(b"META-SECRET", b"different", hashlib.sha256).hexdigest()
        with self.assertRaises(Exception): auth.meta_payload()

    def test_evo_server_apikey_preserves_existing_compatible_webhooks(self):
        self.data["apikey"] = "SERVER-KEY"
        self.assertEqual(auth.evolution(self.data, "shared"), "shared")

    def test_evo_apikey_header_works_without_logging_payload(self):
        self.headers["apikey"] = "SERVER-KEY"
        self.assertEqual(auth.evolution(self.data, "shared"), "shared")

    def test_evo_instance_name_alone_is_rejected(self):
        with self.assertRaises(Exception): auth.evolution(self.data, "shared")

    def test_evo_configured_shared_secret_cannot_be_bypassed_with_server_key(self):
        self.config.evolution_webhook_secret = "SHARED-SECRET"
        self.data["apikey"] = "SERVER-KEY"
        with self.assertRaises(Exception): auth.evolution(self.data, "shared")
        self.headers["X-Webhook-Secret"] = "SHARED-SECRET"
        self.assertEqual(auth.evolution(self.data, "shared"), "shared")

    def test_corrupt_configured_secret_fails_closed(self):
        self.config.evolution_webhook_secret = "********"
        self.config.get_password = lambda *a, **kw: None
        self.data["apikey"] = "SERVER-KEY"
        with self.assertRaises(Exception): auth.evolution(self.data, "shared")

    def test_unknown_instance_rejected_even_with_valid_key(self):
        f.db.exists = lambda *a: False
        self.data["apikey"] = "SERVER-KEY"
        with self.assertRaises(Exception): auth.evolution(self.data, "missing")

    def test_interactive_meta_flow_content_keeps_previous_response_json(self):
        self.assertEqual(auth.interactive_text({"nfm_reply": {"response_json": '{"answer":1}'}}), '{"answer":1}')
        self.assertEqual(auth.interactive_text({"button_reply": {"title": "Yes", "id": "1"}}), "Yes")

class TestPrivateMedia(unittest.TestCase):
    def setUp(self):
        f.local = types.SimpleNamespace(conf={"encryption_key": "DUMMY-SITE-SECRET"})
        f.utils.get_url = lambda: "https://site.example.invalid"
        f.db = types.SimpleNamespace(get_value=Mock(return_value="FILE-1"))
        self.file = FakeDoc(is_private=1, file_name="invoice.pdf", check_permission=Mock(), get_content=lambda: b"DUMMY-PDF")
        f.get_doc = lambda *a: self.file

    def test_local_private_file_has_a_signed_expiring_provider_url(self):
        url = media.provider_url("/private/files/invoice.pdf")
        self.assertIn("secure_media.download?", url)
        self.assertIn("signature=", url)
        self.file.check_permission.assert_called_once_with("read")

    def test_unauthorized_private_file_cannot_be_signed(self):
        self.file.check_permission.side_effect = PermissionError("denied")
        with self.assertRaises(PermissionError): media.provider_url("/private/files/payroll.pdf")

    def test_evo_private_document_send_keeps_caption_and_uses_signed_url(self):
        class Message(base.Message):
            def __getattr__(self, key): return None
        sys.modules["frappe_whatsapp.utils"].evolution = base.evolution
        base.evolution._request = Mock(return_value={"key": {"id": "private-evo"}})
        doc = Message(send_from_instance="shared", attach="/private/files/invoice.pdf", content_type="document", message="Invoice caption")
        doc._send_via_evolution()
        payload = base.evolution._request.call_args.args[-1]
        self.assertIn("secure_media.download?", payload["media"])
        self.assertEqual(payload["caption"], "Invoice caption")
        self.assertEqual(doc.attach, "/private/files/invoice.pdf")

    def test_meta_private_document_send_uses_signed_url(self):
        class Message(base.Message):
            def __getattr__(self, key): return None
        post = Mock(return_value={"messages": [{"id": "private-meta"}]})
        base.controller.make_post_request = post
        doc = Message(message_type="Manual", attach="/private/files/invoice.pdf", content_type="document", message="Invoice caption")
        doc._send_via_meta()
        payload = json.loads(post.call_args.kwargs["data"])
        self.assertIn("secure_media.download?", payload["document"]["link"])
        self.assertEqual(payload["document"]["caption"], "Invoice caption")

    def test_public_and_remote_media_urls_keep_existing_behavior(self):
        self.assertEqual(media.provider_url("/files/logo.png"), "https://site.example.invalid/files/logo.png")
        self.assertEqual(media.provider_url("https://provider.example.invalid/file.pdf"), "https://provider.example.invalid/file.pdf")

    def test_signed_download_works_for_guest_without_general_file_permission(self):
        expiry = int(media.time.time()) + 60
        response = media.download("FILE-1", expiry, media._signature("FILE-1", expiry))
        self.assertEqual(response.data, b"DUMMY-PDF")
        self.assertIn("no-store", response.headers["Cache-Control"])

    def test_expired_tampered_and_different_file_links_are_rejected(self):
        expiry = int(media.time.time()) + 60
        signature = media._signature("FILE-1", expiry)
        for name, when, sig in (("FILE-2", expiry, signature), ("FILE-1", expiry, "bad"), ("FILE-1", 1, signature)):
            with self.subTest(name=name, when=when), self.assertRaises(Exception): media.download(name, when, sig)

class TestIdempotency(unittest.TestCase):
    def test_same_key_different_payload_rejected_without_resending(self):
        key = uuid.uuid4().hex
        self.assertEqual(base.guard.send_once(key, lambda: "first-id", fingerprint="A"), "first-id")
        with self.assertRaises(ValueError): base.guard.send_once(key, lambda: self.fail("must not send"), fingerprint="B")

    def test_sliding_legacy_replay_has_no_five_minute_clock_boundary_gap(self):
        key = uuid.uuid4().hex
        with patch.object(base.guard.time, "time", return_value=299):
            base.guard.send_once(key, lambda: "one", replay_seconds=300)
        with patch.object(base.guard.time, "time", return_value=300):
            self.assertEqual(base.guard.send_once(key, lambda: self.fail("must not send"), replay_seconds=300), "one")

    def test_successful_legacy_send_can_be_intentionally_repeated_later(self):
        key = uuid.uuid4().hex
        with patch.object(base.guard.time, "time", return_value=100):
            base.guard.send_once(key, lambda: "one", replay_seconds=300)
        with patch.object(base.guard.time, "time", return_value=401):
            self.assertEqual(base.guard.send_once(key, lambda: "two", replay_seconds=300), "two")

    def test_uncertain_send_does_not_expire_even_for_legacy_callers(self):
        key = uuid.uuid4().hex
        with self.assertRaises(base.guard.DeliveryUncertain): base.guard.send_once(key, lambda: (_ for _ in ()).throw(TimeoutError()), replay_seconds=300)
        with patch.object(base.guard.time, "time", return_value=10**12):
            with self.assertRaises(base.guard.DeliveryUncertain): base.guard.send_once(key, lambda: self.fail("must not send"), replay_seconds=300)

    def test_receipt_duplicate_is_rejected_by_unique_name(self):
        records = set()
        snapshots = {}
        class Insert:
            def insert(self, set_name, **kw):
                if set_name in records: raise f.DuplicateEntryError()
                records.add(set_name)
        f.get_doc = lambda *a: Insert()
        f.db = types.SimpleNamespace(
            savepoint=lambda name: snapshots.update({name: set(records)}),
            rollback=lambda save_point: (records.clear(), records.update(snapshots[save_point])),
            sql=Mock(),
        )
        self.assertTrue(operations.claim_event("incoming-evo", "instance:mid"))
        self.assertFalse(operations.claim_event("incoming-evo", "instance:mid"))
        self.assertTrue(operations.claim_event("incoming-evo", "other-instance:mid"))
        operations.transaction_lock("lead-phone", "966500000000")
        self.assertIn("FOR UPDATE", f.db.sql.call_args.args[0])


class TestNotificationCompatibility(unittest.TestCase):
    def setUp(self):
        f.flags = base.Flags()
        f.session = base.Flags(user="invoice-user")
        f.get_roles = lambda *a: []
        f.has_permission = Mock(side_effect=PermissionError("no manual send permission"))
        self.log = []
        class Message(base.Message):
            def __getattr__(self, name): return None
            def insert(doc, **kw):
                doc.before_insert()
                self.log.append(doc)
                return doc
        self.template = FakeDoc(name="tpl", template="Invoice {{1}}", actual_name="invoice", language_code="en", header_type="", field_names="", sample_values="")
        def get_doc(*args):
            if isinstance(args[0], dict):
                if args[0]["doctype"] == "WhatsApp Message": return Message(**args[0])
                return FakeDoc(insert=lambda **kw: None)
            if args[0] == "WhatsApp Templates": return self.template
            raise AssertionError(args)
        f.get_doc = get_doc
        f.db = types.SimpleNamespace(get_value=lambda dt, *a, **kw: self.template if dt == "WhatsApp Templates" else "server")
        self.meta = Mock(return_value={"messages": [{"id": "meta-notification"}]})
        self.evo = Mock(return_value={"key": {"id": "evo-notification"}})
        base.controller.make_post_request = self.meta
        base.evolution._request = self.evo
        sys.modules["frappe_whatsapp.utils"].evolution = base.evolution
        sys.modules["frappe_whatsapp.utils.evolution"] = base.evolution
        class Notification(notification.WhatsAppNotification):
            def __getattr__(self, key): return None
            def get(self, key): return getattr(self, key, None)
        self.rule = Notification()
        self.rule.name = uuid.uuid4().hex
        self.rule.template = "tpl"
        self.rule.code = "Invoice"
        self.rule.fields = []
        self.rule.resolve_phone = lambda *a: "966500000000"
        self.data = {"doctype": "Sales Invoice", "name": "INV-1", "grand_total": 100, "modified": "old"}
        self.doc = FakeDoc(as_dict=lambda: dict(self.data))

    def test_evolution_invoice_automation_keeps_working_without_granting_manual_send(self):
        sys.modules["frappe_whatsapp.utils.messaging"].get_default_channel = lambda: "Evolution"
        self.rule.send_template_message(self.doc)
        self.data["modified"] = "new-timestamp"
        self.rule.send_template_message(self.doc)
        self.assertEqual(self.evo.call_count, 1)
        self.assertEqual(self.log[-1].message_id, "evo-notification")
        self.assertFalse(f.flags.whatsapp_configured_automation)

    def test_meta_invoice_automation_uses_one_native_template_request(self):
        sys.modules["frappe_whatsapp.utils.messaging"].get_default_channel = lambda: "Meta"
        self.rule.send_template_message(self.doc)
        self.rule.send_template_message(self.doc)
        self.assertEqual(self.meta.call_count, 1)
        self.assertEqual(json.loads(self.meta.call_args.kwargs["data"])["type"], "template")
        self.assertEqual(self.log[-1].message_id, "meta-notification")

    def test_changed_invoice_can_send_a_new_notification(self):
        sys.modules["frappe_whatsapp.utils.messaging"].get_default_channel = lambda: "Evolution"
        self.rule.send_template_message(self.doc)
        self.data["grand_total"] = 200
        self.rule.send_template_message(self.doc)
        self.assertEqual(self.evo.call_count, 2)


class TestWebhookProcessing(unittest.TestCase):
    def setUp(self):
        self.inserted = []
        self.claimed = set()
        self.config = settings(meta_app_secret="META-SECRET", evolution_webhook_secret="")
        self.headers = {}
        self.data = {}
        self.raw = b""
        f.form_dict = {}
        f.get_request_header = lambda name: self.headers.get(name, "")
        f.request = types.SimpleNamespace(method="POST", get_data=lambda: self.raw, get_json=lambda **kw: self.data)
        f.get_cached_doc = lambda *a: self.config
        f.db = types.SimpleNamespace(
            exists=lambda dt, *a: dt == "Whatsapp Instance",
            get_single_value=lambda *a: "phone",
        )
        def get_doc(*args):
            if isinstance(args[0], dict):
                data = args[0]
                return FakeDoc(insert=lambda **kw: self.inserted.append(data))
            if args[0] == "WhatsApp Settings": return self.config
            if args[0] == "Whatsapp Instance": return FakeDoc(evolution_server="server")
            if args[0] == "Evolution Server": return FakeDoc(get_api_key=lambda: "SERVER-KEY")
            raise AssertionError(args)
        f.get_doc = get_doc
        def claim(namespace, identity):
            key = (namespace, identity)
            if key in self.claimed: return False
            self.claimed.add(key)
            return True
        self.claim = claim
        self.patchers = [patch.object(webhook, "claim_event", side_effect=claim), patch.object(auth, "claim_event", side_effect=claim)]
        for item in self.patchers: item.start()

    def tearDown(self):
        for item in reversed(self.patchers): item.stop()

    def test_valid_meta_webhook_replay_creates_only_one_incoming_message(self):
        self.data = {"entry": [{"changes": [{"field": "messages", "value": {"messages": [{"type": "text", "from": "966500000000", "id": "meta-mid", "text": {"body": "hello"}}]}}]}]}
        self.raw = json.dumps(self.data).encode()
        self.headers["X-Hub-Signature-256"] = "sha256=" + hmac.new(b"META-SECRET", self.raw, hashlib.sha256).hexdigest()
        webhook.post(); webhook.post()
        messages = [doc for doc in self.inserted if doc["doctype"] == "WhatsApp Message"]
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["message"], "hello")

    def test_evolution_text_replay_preserves_instance_and_hides_credentials_from_log(self):
        self.data = {"instance": "shared", "apikey": "SERVER-KEY", "event": "messages.upsert", "data": {"key": {"id": "evo-mid", "remoteJid": "966500000000@s.whatsapp.net"}, "message": {"conversation": "hello"}}}
        self.raw = json.dumps(self.data).encode()
        webhook.evolution_webhook(); webhook.evolution_webhook()
        messages = [doc for doc in self.inserted if doc["doctype"] == "WhatsApp Message"]
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["send_from_instance"], "shared")
        logs = [doc for doc in self.inserted if doc["doctype"] == "WhatsApp Notification Log"]
        self.assertNotIn("SERVER-KEY", json.dumps(logs))
        self.assertNotIn("hello", json.dumps(logs))

    def test_meta_batch_processes_every_entry(self):
        value = lambda mid: {"field": "messages", "value": {"messages": [{"type": "text", "from": "966500000000", "id": mid, "text": {"body": "hello"}}]}}
        self.data = {"entry": [{"changes": [value("one")]}, {"changes": [value("two")]}]}
        self.raw = json.dumps(self.data).encode()
        self.headers["X-Hub-Signature-256"] = "sha256=" + hmac.new(b"META-SECRET", self.raw, hashlib.sha256).hexdigest()
        webhook.post()
        self.assertEqual(len([doc for doc in self.inserted if doc["doctype"] == "WhatsApp Message"]), 2)

    def test_evo_status_is_scoped_to_authenticated_instance(self):
        queries = []
        updates = []
        def value(dt, filters, field):
            queries.append(filters)
            return "message-row" if isinstance(filters, dict) else "Sent"
        f.db = types.SimpleNamespace(get_value=value, set_value=lambda *a: updates.append(a), sql=Mock())
        webhook._process_evolution_status({"key": {"id": "mid"}, "status": "READ"}, "trusted-instance")
        self.assertEqual(queries[0]["send_from_instance"], "trusted-instance")
        self.assertEqual(queries[0]["channel"], "Evolution")
        self.assertEqual(updates[0][-1], "Read")

    def test_late_status_does_not_downgrade_delivered_message(self):
        f.db = types.SimpleNamespace(get_value=lambda dt, filters, field: "row" if isinstance(filters, dict) else "Delivered", set_value=Mock(), sql=Mock())
        webhook._process_evolution_status({"key": {"id": "mid"}, "status": "SENT"}, "trusted-instance")
        f.db.set_value.assert_not_called()


if __name__ == "__main__":
    unittest.main()
