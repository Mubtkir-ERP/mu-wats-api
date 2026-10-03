# Copyright (c) 2025, Shridhar Patil and contributors
# For license information, please see license.txt

import json
import random
import time
import frappe
from frappe import _
from frappe.model.document import Document
from frappe.model.naming import make_autoname
from frappe.utils import cint, get_datetime, now_datetime

from frappe_whatsapp.utils.messaging import ensure_enabled, get_default_channel, normalize_number


class BulkWhatsAppMessage(Document):
    def autoname(self):
        self.name = make_autoname("BULK-WA-.YYYY.-.#####")

    def validate(self):
        self.validate_recipients()
        self.validate_message()
        self.validate_attachment()

    def validate_message(self):
        if self.use_template and not self.template:
            frappe.throw(_("Select a WhatsApp Template or disable Use Template."))
        if not self.use_template and not (self.message_content or self.attach):
            frappe.throw(_("Message Content or an attachment is required."))
        if cint(self.message_delay) < 0 or cint(self.max_delay) < 0:
            frappe.throw(_("Message delay cannot be negative."))
        if cint(self.max_delay) and cint(self.message_delay) > cint(self.max_delay):
            frappe.throw(_("Max Delay must be greater than or equal to Min Delay."))

    def validate_attachment(self):
        if not self.attach:
            return
        max_mb = frappe.db.get_single_value("WhatsApp Settings", "max_attachment_size") or 16
        file_size = frappe.db.get_value("File", {"file_url": self.attach}, "file_size")
        if file_size and file_size > cint(max_mb) * 1024 * 1024:
            frappe.throw(
                _("The attachment is {0} MB, over the configured {1} MB limit.").format(
                    round(file_size / (1024 * 1024), 1), max_mb
                ),
                title=_("Attachment Too Large"),
            )

    def validate_recipients(self):
        if not self.recipients and not self.recipient_list:
            frappe.throw(_("At least one recipient or a recipient list is required"))
        if self.recipient_type == "Recipient List" and self.recipient_list:
            count = frappe.db.count("WhatsApp Recipient", {"parent": self.recipient_list})
            if not count:
                frappe.throw(_("Selected recipient list has no recipients"))
            self.recipient_count = count
        else:
            self.recipient_count = len(self.recipients or [])

    def on_submit(self):
        ensure_enabled()
        if self.scheduled_time and get_datetime(self.scheduled_time) > now_datetime():
            self.db_set("status", "Queued")
            return
        self.enqueue_send()

    def enqueue_send(self, retry_failed_only=False):
        """Queue a campaign on Frappe's long worker; never block the web request."""
        ensure_enabled()
        self.db_set("status", "In Progress")
        try:
            frappe.enqueue(
                "frappe_whatsapp.frappe_whatsapp.doctype.bulk_whatsapp_message.bulk_whatsapp_message.process_bulk_message",
                queue="long",
                enqueue_after_commit=True,
                job_name=f"whatsapp-bulk-{self.name}{'-retry' if retry_failed_only else ''}",
                bulk_name=self.name,
                retry_failed_only=retry_failed_only,
            )
        except Exception:
            self.db_set("status", "Queued" if not retry_failed_only else "Partially Failed")
            raise

    def _resolve_sender(self):
        from frappe_whatsapp.utils import evolution

        base_url, api_key, instance_name = evolution.resolve_instance(
            self.sender_number or None,
            user=self.owner,
        )
        self._sender_base = base_url
        self._sender_key = api_key
        self._sender_instance = instance_name
        if not self.sender_number:
            self.db_set("sender_number", instance_name, update_modified=False)

    def _delay_between_messages(self):
        minimum = cint(self.message_delay)
        maximum = cint(self.max_delay)
        if maximum < minimum:
            maximum = minimum
        if maximum <= 0:
            return
        time.sleep(random.randint(minimum, maximum))

    def send_messages(self, retry_failed_only=False):
        """Worker-side campaign processor."""
        ensure_enabled()
        self._channel = get_default_channel()
        self._sender_instance = None
        self._sender_base = None
        self._sender_key = None
        if self._channel == "Evolution":
            self._resolve_sender()
        self._send_errors = []
        self._log_lines = []

        if retry_failed_only:
            return self._retry_failed_messages()

        recipients = self._gather_recipients()
        total = len(recipients)
        sent = 0
        failed = 0
        self.db_set("sent_count", 0, update_modified=False)

        try:
            for i, recipient in enumerate(recipients):
                if i:
                    self._delay_between_messages()
                frappe.publish_progress(
                    percent=int((i / total) * 100) if total else 100,
                    title=_("Sending WhatsApp Messages"),
                    description=_("Sending message {0} of {1} to {2}").format(
                        i + 1,
                        total,
                        recipient.get("recipient_name") or recipient.get("mobile_number"),
                    ),
                )
                if self.send_single_message(recipient):
                    sent += 1
                    self.db_set("sent_count", sent, update_modified=False)
                else:
                    failed += 1

            self._finish(sent, failed)
            return {"sent": sent, "failed": failed}
        except Exception:
            self.db_set("status", "Failed")
            self._log(f"FATAL: {frappe.get_traceback()[-2000:]}")
            self._persist_log()
            raise

    def _finish(self, sent, failed):
        if failed == 0:
            status = "Completed"
        elif sent == 0:
            status = "Failed"
        else:
            status = "Partially Failed"
        self.db_set("status", status)
        self.db_set("sent_count", sent, update_modified=False)
        self._persist_log()
        frappe.publish_progress(
            percent=100,
            title=_("Sending WhatsApp Messages"),
            description=_("Completed: {0} sent, {1} failed").format(sent, failed),
        )

    def _persist_log(self):
        if self._log_lines:
            self.db_set("send_log", "\n".join(self._log_lines)[-50000:], update_modified=False)

    def _build_message_text(self, recipient):
        if not (self.use_template and self.template):
            return self.message_content or ""

        template = frappe.db.get_value("WhatsApp Templates", self.template, fieldname="*")
        if not template:
            frappe.throw(_("Template {0} not found").format(self.template))

        parameters = []
        if recipient.get("recipient_data") and self.variable_type == "Unique":
            try:
                values = json.loads(recipient.get("recipient_data") or "{}")
                parameters = list(values.values()) if isinstance(values, dict) else list(values)
            except Exception:
                parameters = []
        elif self.template_variables and self.variable_type == "Common":
            try:
                values = json.loads(self.template_variables)
                parameters = list(values.values()) if isinstance(values, dict) else list(values)
            except Exception:
                parameters = []

        text = template.get("template") or ""
        for i, value in enumerate(parameters, 1):
            text = text.replace("{{%s}}" % i, str(value or ""))
        return text

    def _gather_recipients(self):
        if self.recipient_type == "Recipient List" and self.recipient_list:
            return frappe.get_all(
                "WhatsApp Recipient",
                filters={"parent": self.recipient_list},
                fields=["mobile_number", "name", "recipient_name", "recipient_data"],
                order_by="idx asc",
            )
        return [
            {
                "mobile_number": r.mobile_number,
                "recipient_name": r.recipient_name,
                "recipient_data": r.recipient_data or "{}",
            }
            for r in (self.recipients or [])
        ]

    @frappe.whitelist()
    def preview_messages(self, limit=25):
        limit = max(1, min(int(limit or 25), 100))
        return [
            {
                "mobile": r.get("mobile_number"),
                "name": r.get("recipient_name"),
                "message": self._build_message_text(r),
            }
            for r in self._gather_recipients()[:limit]
        ]

    def _message_parameters(self, recipient):
        if not self.use_template:
            return None
        if self.variable_type == "Unique":
            return recipient.get("recipient_data") or "{}"
        return self.template_variables or "{}"

    def send_single_message(self, recipient):
        """Send one campaign recipient through the global channel router."""
        phone_number = normalize_number(recipient.get("mobile_number"))
        if not phone_number:
            self._record_failure("", "", _("Recipient has no mobile number"), recipient=recipient)
            return False

        message_text = self._build_message_text(recipient)
        filename = self.attach.rsplit("/", 1)[-1] if self.attach else None
        content_type = _media_kind(filename)[0] if filename else "text"
        data = {
            "doctype": "WhatsApp Message",
            "type": "Outgoing",
            "message": message_text,
            "to": phone_number,
            "message_type": "Template" if self.use_template else "Manual",
            "content_type": content_type,
            "bulk_message_reference": self.name,
            "channel": self._channel,
        }
        if self.attach:
            data["attach"] = self.attach
        if self._channel == "Evolution" and self._sender_instance:
            data["send_from_instance"] = self._sender_instance
        if self.use_template:
            data.update(
                {
                    "use_template": 1,
                    "template": self.template,
                    "template_parameters": self._message_parameters(recipient),
                }
            )

        try:
            msg = frappe.get_doc(data)
            msg.insert(ignore_permissions=True)
            self._log(
                f"✓ {phone_number} | {msg.channel} | {msg.content_type or content_type} | "
                f"{msg.message_id or 'accepted'}"
            )
            return True
        except Exception as exc:
            self._record_failure(phone_number, message_text, str(exc), recipient=recipient)
            return False

    def _record_failure(self, phone_number, message_text, error_message, recipient=None):
        error_message = (error_message or _("Unknown error"))[:1000]
        self._send_errors.append(error_message)
        self._log(f"✗ {phone_number or '(no number)'} | {self._channel} | FAILED: {error_message}")
        frappe.log_error(
            f"Failed to send WhatsApp message to {phone_number}: {error_message}",
            "WhatsApp Bulk Messaging",
        )
        try:
            filename = self.attach.rsplit("/", 1)[-1] if self.attach else None
            content_type = _media_kind(filename)[0] if filename else "text"
            data = {
                "doctype": "WhatsApp Message",
                "type": "Outgoing",
                "message": message_text or "",
                "to": phone_number,
                "message_type": "Template" if self.use_template else "Manual",
                "content_type": content_type,
                "status": "Failed",
                "failure_reason": error_message,
                "bulk_message_reference": self.name,
                "channel": self._channel,
            }
            if self.attach:
                data["attach"] = self.attach
            if self._channel == "Evolution" and self._sender_instance:
                data["send_from_instance"] = self._sender_instance
            if self.use_template:
                data.update(
                    {
                        "use_template": 1,
                        "template": self.template,
                        "template_parameters": self._message_parameters(recipient or {}),
                    }
                )
            msg = frappe.get_doc(data)
            msg.flags.skip_meta_send = True
            msg.insert(ignore_permissions=True)
        except Exception:
            frappe.log_error(frappe.get_traceback(), "Could not store failed WhatsApp Message")

    def _log(self, line):
        self._log_lines.append(str(line))

    def format_number(self, number):
        return normalize_number(number)

    @frappe.whitelist()
    def retry_failed(self):
        """Retry this campaign's failed messages on the long worker."""
        failed = frappe.db.count(
            "WhatsApp Message", {"bulk_message_reference": self.name, "status": "Failed"}
        )
        if not failed:
            return {"queued": 0}
        self.enqueue_send(retry_failed_only=True)
        return {"queued": failed}

    def _retry_failed_messages(self):
        names = frappe.get_all(
            "WhatsApp Message",
            filters={"bulk_message_reference": self.name, "status": "Failed"},
            pluck="name",
            order_by="creation asc",
        )
        sent = 0
        failed = 0
        for i, name in enumerate(names):
            if i:
                self._delay_between_messages()
            try:
                msg = frappe.get_doc("WhatsApp Message", name)
                msg.to = normalize_number(msg.to)
                msg.channel = self._channel
                msg.send_from_instance = self._sender_instance if self._channel == "Evolution" else None
                msg.message_id = None
                msg.failure_reason = ""

                if self._channel == "Evolution":
                    if msg.message_type == "Template" and not msg.message:
                        msg.message = msg._render_evolution_template()
                    msg._send_via_evolution()
                else:
                    msg._send_via_meta()

                frappe.db.set_value(
                    "WhatsApp Message",
                    name,
                    {
                        "status": msg.status or "Sent",
                        "message_id": msg.message_id,
                        "content_type": msg.content_type,
                        "channel": self._channel,
                        "send_from_instance": msg.send_from_instance,
                        "failure_reason": "",
                    },
                )
                sent += 1
                self._log(f"✓ RETRY {msg.to} | {self._channel} | {msg.message_id or 'accepted'}")
            except Exception as exc:
                failed += 1
                frappe.db.set_value("WhatsApp Message", name, "failure_reason", str(exc)[:1000])
                self._log(f"✗ RETRY {name} | {self._channel} | {exc}")

        total_sent = frappe.db.count(
            "WhatsApp Message",
            {"bulk_message_reference": self.name, "status": ["in", ["Sent", "Delivered", "Read"]]},
        )
        remaining_failed = frappe.db.count(
            "WhatsApp Message", {"bulk_message_reference": self.name, "status": "Failed"}
        )
        self._finish(total_sent, remaining_failed)
        return {"retried": len(names), "sent": sent, "failed": failed}

    @frappe.whitelist()
    def get_progress(self):
        total = cint(self.recipient_count)
        sent = frappe.db.count(
            "WhatsApp Message",
            {"bulk_message_reference": self.name, "status": ["in", ["Sent", "Delivered", "Read"]]},
        )
        failed = frappe.db.count(
            "WhatsApp Message", {"bulk_message_reference": self.name, "status": "Failed"}
        )
        return {
            "total": total,
            "sent": sent,
            "failed": failed,
            "queued": max(total - sent - failed, 0),
            "percent": (sent / total * 100) if total else 0,
        }


_MEDIA_EXT = {
    "image": {"jpg", "jpeg", "png", "gif", "webp", "bmp"},
    "video": {"mp4", "3gp", "mov", "mkv", "webm"},
    "audio": {"mp3", "ogg", "oga", "opus", "aac", "m4a", "amr", "wav"},
}
_DOC_MIME = {
    "pdf": "application/pdf",
    "doc": "application/msword",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xls": "application/vnd.ms-excel",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "csv": "text/csv",
    "txt": "text/plain",
    "zip": "application/zip",
}


def _media_kind(filename):
    ext = filename.rsplit(".", 1)[-1].lower() if filename and "." in filename else ""
    for kind, exts in _MEDIA_EXT.items():
        if ext in exts:
            return kind, None
    return "document", _DOC_MIME.get(ext) or "application/octet-stream"


def process_bulk_message(bulk_name, retry_failed_only=False):
    """Background-worker entry point with a durable failure state."""
    try:
        doc = frappe.get_doc("Bulk WhatsApp Message", bulk_name)
        if doc.docstatus != 1:
            return
        return doc.send_messages(retry_failed_only=bool(retry_failed_only))
    except Exception:
        # Background jobs are transactional. Roll back the failed send work,
        # then persist a visible campaign failure before re-raising so the job
        # is still marked failed in the worker dashboard.
        frappe.db.rollback()
        if frappe.db.exists("Bulk WhatsApp Message", bulk_name):
            frappe.db.set_value("Bulk WhatsApp Message", bulk_name, "status", "Failed")
            frappe.db.commit()
        frappe.log_error(frappe.get_traceback(), f"WhatsApp bulk worker failed: {bulk_name}")
        raise


def trigger_scheduled_bulk_messages():
    """Queue submitted campaigns whose Scheduled Time has arrived."""
    if frappe.flags.in_import or frappe.flags.in_patch:
        return
    names = frappe.get_all(
        "Bulk WhatsApp Message",
        filters={
            "docstatus": 1,
            "status": "Queued",
            "scheduled_time": ["<=", now_datetime()],
        },
        pluck="name",
    )
    for name in names:
        try:
            doc = frappe.get_doc("Bulk WhatsApp Message", name)
            doc.enqueue_send()
        except Exception:
            frappe.log_error(frappe.get_traceback(), f"Could not queue WhatsApp bulk campaign: {name}")


@frappe.whitelist()
def send_bulk_interactive(recipients, payload, instance_name=None):
    """Send one buttons/list payload to many recipients through Evolution."""
    from frappe_whatsapp.utils import evolution

    frappe.has_permission("Bulk WhatsApp Message", "create", throw=True)
    ensure_enabled()
    if isinstance(recipients, str):
        recipients = frappe.parse_json(recipients)
    if isinstance(payload, str):
        payload = frappe.parse_json(payload)

    base_url, api_key, instance_name = evolution.resolve_instance(instance_name)
    numbers = [r.get("mobile_number") if isinstance(r, dict) else r for r in recipients]
    sent, failed, errors = 0, 0, []

    for number in numbers:
        number = normalize_number(number)
        if not number:
            continue
        try:
            if payload.get("buttons"):
                mid = evolution.send_buttons(
                    number=number,
                    title=payload.get("title", ""),
                    description=payload.get("description", ""),
                    buttons=payload.get("buttons", []),
                    footer=payload.get("footer"),
                    base_url=base_url,
                    api_key=api_key,
                    instance_name=instance_name,
                )
                ctype = "button"
            else:
                mid = evolution.send_list(
                    number=number,
                    title=payload.get("title", ""),
                    description=payload.get("description", ""),
                    button_text=payload.get("button_text") or payload.get("buttonText") or "Select",
                    sections=payload.get("sections", []),
                    footer=payload.get("footer"),
                    base_url=base_url,
                    api_key=api_key,
                    instance_name=instance_name,
                )
                ctype = "flow"

            log = frappe.get_doc(
                {
                    "doctype": "WhatsApp Message",
                    "type": "Outgoing",
                    "status": "Sent",
                    "to": number,
                    "message": payload.get("description", ""),
                    "message_id": mid,
                    "content_type": ctype,
                    "channel": "Evolution",
                    "send_from_instance": instance_name,
                }
            )
            log.flags.skip_meta_send = True
            log.insert(ignore_permissions=True)
            sent += 1
        except Exception as exc:
            failed += 1
            errors.append(f"{number}: {exc}")
            frappe.log_error(f"{number}\n{frappe.get_traceback()}", "Bulk Interactive Send")

    return {"sent": sent, "failed": failed, "errors": errors}
