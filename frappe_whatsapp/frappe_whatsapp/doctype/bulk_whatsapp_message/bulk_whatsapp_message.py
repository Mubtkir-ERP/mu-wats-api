# Copyright (c) 2025, Shridhar Patil and contributors
# For license information, please see license.txt

from datetime import timedelta
import json
import random
import re
import time
import frappe
from frappe import _
from frappe.model.document import Document
from frappe.model.naming import make_autoname
from frappe.utils import cint, get_datetime, now_datetime

from frappe_whatsapp.utils.messaging import ensure_enabled, get_default_channel, normalize_number


def process_spintax(text):
    """Resolve spintax choices like {Option 1|Option 2|Option 3}.

    Only replaces braces that contain at least one pipe symbol (|) so that
    single-word placeholders like {name} or {mobile} are preserved for substitution.
    """
    if not text:
        return text
    pattern = re.compile(r"\{([^{}]+)\}")
    for _ in range(15):
        found = False
        for match in pattern.finditer(text):
            inner = match.group(1)
            if "|" in inner:
                choices = inner.split("|")
                selected = random.choice(choices)
                text = text[:match.start()] + selected + text[match.end():]
                found = True
                break
        if not found:
            break
    return text


def replace_placeholders(text, recipient):
    """Replace personal placeholders ({name}, {mobile}, custom json) with recipient data."""
    if not text:
        return text
    name = (recipient.get("recipient_name") or "").strip()
    number = (recipient.get("mobile_number") or "").strip()

    text = text.replace("{name}", name).replace("{{name}}", name)
    text = text.replace("{recipient_name}", name).replace("{{recipient_name}}", name)
    text = text.replace("{mobile}", number).replace("{{mobile}}", number)
    text = text.replace("{mobile_number}", number).replace("{{mobile_number}}", number)

    data = recipient.get("recipient_data")
    if data:
        try:
            parsed = json.loads(data) if isinstance(data, str) else data
            if isinstance(parsed, dict):
                for k, v in parsed.items():
                    val = str(v if v is not None else "")
                    text = text.replace("{" + k + "}", val).replace("{{" + k + "}}", val)
        except Exception:
            pass
    return text


class BulkWhatsAppMessage(Document):
    def autoname(self):
        self.name = make_autoname("BULK-WA-.YYYY.-.#####")

    def validate(self):
        self.validate_recipients()
        self.validate_message()
        self.validate_attachment()

    def validate_message(self):
        if self.use_template:
            mode = self.template_mode or "Single Template"
            if mode == "Single Template" and not self.template:
                frappe.throw(_("Select a WhatsApp Template or choose another Template Mode."))
            elif mode == "Rotate Multiple Templates":
                valid_templates = [t for t in (self.rotating_templates or []) if t.template]
                if not valid_templates:
                    frappe.throw(_("Please add at least one template in the Rotating Templates table."))
        if not self.use_template and not (self.message_content or self.attach):
            frappe.throw(_("Message Content or an attachment is required."))
        if cint(self.message_delay) < 0 or cint(self.max_delay) < 0:
            frappe.throw(_("Message delay cannot be negative."))
        if cint(self.max_delay) and cint(self.message_delay) > cint(self.max_delay):
            frappe.throw(_("Max Delay must be greater than or equal to Min Delay."))
        if self.enable_batch_sending:
            if cint(self.batch_size) <= 0:
                frappe.throw(_("Batch Size must be greater than 0."))
            if float(self.batch_delay_hours or 0) < 0:
                frappe.throw(_("Cool-down hours between batches cannot be negative."))

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

    def _get_recipient_template(self, recipient, recipient_index=0):
        if not self.use_template:
            return None
        mode = self.template_mode or "Single Template"
        if mode == "Per Recipient" and recipient.get("template"):
            return recipient.get("template")
        elif mode == "Rotate Multiple Templates" and self.rotating_templates:
            valid = [r.template for r in self.rotating_templates if r.template]
            if valid:
                return valid[recipient_index % len(valid)]
        return self.template

    def _build_message_text(self, recipient, recipient_index=0):
        if not self.use_template:
            raw_text = process_spintax(self.message_content or "")
            return replace_placeholders(raw_text, recipient), None

        template_name = self._get_recipient_template(recipient, recipient_index)
        if not template_name:
            template_name = self.template
        if not template_name:
            raw_text = process_spintax(self.message_content or "")
            return replace_placeholders(raw_text, recipient), None

        template = frappe.db.get_value("WhatsApp Templates", template_name, fieldname="*")
        if not template:
            frappe.throw(_("Template {0} not found").format(template_name))

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

        text = process_spintax(text)
        return replace_placeholders(text, recipient), template_name

    def _gather_recipients(self):
        recipients = []
        seen = set()
        for recipient in self._gather_recipients_raw():
            number = normalize_number(recipient.get("mobile_number"))
            if number and number in seen:
                continue
            if number:
                seen.add(number)
            recipients.append(recipient)
        return recipients

    def _gather_recipients_raw(self):
        if self.recipient_type == "Recipient List" and self.recipient_list:
            return frappe.get_all(
                "WhatsApp Recipient",
                filters={"parent": self.recipient_list},
                fields=["mobile_number", "name", "recipient_name", "template", "recipient_data"],
                order_by="idx asc",
            )
        return [
            {
                "mobile_number": r.mobile_number,
                "recipient_name": r.recipient_name,
                "template": getattr(r, "template", None),
                "recipient_data": r.recipient_data or "{}",
            }
            for r in (self.recipients or [])
        ]

    @frappe.whitelist()
    def preview_messages(self, limit=25):
        limit = max(1, min(int(limit or 25), 100))
        recipients = self._gather_recipients()[:limit]
        out = []
        for idx, r in enumerate(recipients):
            msg_text, tpl = self._build_message_text(r, recipient_index=idx)
            out.append({
                "mobile": r.get("mobile_number"),
                "name": r.get("recipient_name"),
                "template": tpl if self.use_template else None,
                "message": msg_text,
            })
        return out

    def _message_parameters(self, recipient):
        if not self.use_template:
            return None
        if self.variable_type == "Unique":
            return recipient.get("recipient_data") or "{}"
        return self.template_variables or "{}"

    def send_single_message(self, recipient, recipient_index=0):
        """Send one campaign recipient through the global channel router."""
        phone_number = normalize_number(recipient.get("mobile_number"))
        if phone_number and frappe.db.exists("WhatsApp Message", {
            "bulk_message_reference": self.name, "to": phone_number,
            "status": ["in", ["Sent", "Delivered", "Read"]],
        }):
            return True
        if not phone_number:
            self._record_failure("", "", _("Recipient has no mobile number"), recipient=recipient)
            return False

        message_text, template_used = self._build_message_text(recipient, recipient_index)
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
                    "template": template_used,
                    "template_parameters": self._message_parameters(recipient),
                }
            )

        try:
            msg = frappe.get_doc(data)
            msg.insert(ignore_permissions=True)
            self._log(
                f"✓ {phone_number} | {msg.channel} | {msg.content_type or content_type} | "
                f"tpl={template_used or '-'} | {msg.message_id or 'accepted'}"
            )
            return True
        except Exception as exc:
            self._record_failure(phone_number, message_text, str(exc), recipient=recipient, template_used=template_used)
            return False

    def _record_failure(self, phone_number, message_text, error_message, recipient=None, template_used=None):
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
                        "template": template_used or self.template,
                        "template_parameters": self._message_parameters(recipient or {}),
                    }
                )
            msg = frappe.get_doc(data)
            msg.flags.skip_meta_send = True
            msg.insert(ignore_permissions=True)
        except Exception:
            frappe.log_error(frappe.get_traceback(), "Could not store failed WhatsApp Message")

    def send_messages(self, retry_failed_only=False):
        """Worker-side campaign processor with anti-ban batching and cooling."""
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

        # Retrieve set of numbers already accepted in this campaign
        already_sent_numbers = set(
            frappe.get_all(
                "WhatsApp Message",
                filters={
                    "bulk_message_reference": self.name,
                    "status": ["in", ["Sent", "Delivered", "Read"]],
                },
                pluck="to",
            )
        )

        sent = len(already_sent_numbers)
        failed = 0
        self.db_set("sent_count", sent, update_modified=False)

        # Batch & Throttling calculation
        use_batch = bool(self.enable_batch_sending)
        batch_limit = cint(self.batch_size) or 20
        if use_batch and self.enable_warmup:
            batches_done = cint(self.completed_batches)
            every = max(1, cint(self.warmup_after_batches) or 2)
            inc = cint(self.warmup_increment) or 10
            max_limit = cint(self.max_batch_size) or 50
            batch_limit = min(max_limit, batch_limit + (batches_done // every) * inc)

        batch_sent_count = 0
        pause_for_next_batch = False

        try:
            for i, recipient in enumerate(recipients):
                phone = normalize_number(recipient.get("mobile_number"))
                if phone and phone in already_sent_numbers:
                    continue

                # Check if current batch limit reached
                if use_batch and batch_sent_count >= batch_limit:
                    pause_for_next_batch = True
                    break

                if batch_sent_count > 0:
                    self._delay_between_messages()

                frappe.publish_progress(
                    percent=int((sent / total) * 100) if total else 100,
                    title=_("Sending WhatsApp Messages"),
                    description=_("Sending message {0} of {1} to {2}").format(
                        sent + 1,
                        total,
                        recipient.get("recipient_name") or recipient.get("mobile_number"),
                    ),
                )

                if self.send_single_message(recipient, recipient_index=i):
                    sent += 1
                    batch_sent_count += 1
                    if phone:
                        already_sent_numbers.add(phone)
                    self.db_set("sent_count", sent, update_modified=False)
                else:
                    failed += 1

            if pause_for_next_batch:
                new_completed = cint(self.completed_batches) + 1
                hours = float(self.batch_delay_hours or 24)
                if hours <= 0:
                    hours = 24.0
                next_time = now_datetime() + timedelta(hours=hours)
                self.db_set("completed_batches", new_completed)
                self.db_set("scheduled_time", next_time)
                self.db_set("next_batch_time", next_time)
                self.db_set("status", "Queued")
                remaining = max(0, total - sent)
                self._log(
                    f"⏳ Batch {new_completed} complete: Sent {batch_sent_count} messages in this batch (Target was {batch_limit}). "
                    f"{remaining} recipients remaining. Cooling down for {hours} hours. "
                    f"Next batch scheduled at {next_time}."
                )
                self._persist_log()
                frappe.publish_progress(
                    percent=int((sent / total) * 100) if total else 100,
                    title=_("Batch Complete - Cooling Down"),
                    description=_("Batch {0} complete ({1} sent). Resuming at {2}").format(
                        new_completed, batch_sent_count, next_time
                    ),
                )
                return {
                    "sent": sent,
                    "batch_sent": batch_sent_count,
                    "status": "Queued",
                    "next_batch_time": str(next_time),
                }

            self._finish(sent, failed)
            self.db_set("next_batch_time", None)
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

    def _log(self, line):
        self._log_lines.append(str(line))

    def format_number(self, number):
        return normalize_number(number)

    @frappe.whitelist()
    def send_next_batch_now(self):
        """Immediately trigger the next batch without waiting for the scheduled cooldown."""
        self.check_permission("write")
        if self.docstatus != 1 or self.status == "Completed":
            frappe.throw(_("Campaign is either not submitted or already completed."))
        self.db_set("scheduled_time", now_datetime())
        self.enqueue_send()
        return {"queued": True}

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
                frappe.db.sql("SELECT name FROM `tabWhatsApp Message` WHERE name=%s FOR UPDATE", (name,))
                msg.reload()
                if msg.status != "Failed":
                    continue
                msg.to = normalize_number(msg.to)
                msg.channel = self._channel
                msg.send_from_instance = self._sender_instance if self._channel == "Evolution" else None
                if msg.message_id:
                    self._log(f"SKIP RETRY {name}: provider message ID already exists")
                    continue
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
            "completed_batches": cint(self.completed_batches),
            "next_batch_time": str(self.next_batch_time) if self.next_batch_time else None,
            "enable_batch_sending": bool(self.enable_batch_sending),
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
        if doc.docstatus != 1 or (not retry_failed_only and doc.status == "Completed"):
            return
        return doc.send_messages(retry_failed_only=bool(retry_failed_only))
    except Exception:
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
