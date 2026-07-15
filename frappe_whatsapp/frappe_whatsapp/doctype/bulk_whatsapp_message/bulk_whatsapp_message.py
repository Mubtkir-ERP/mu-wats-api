# Bulk WhatsApp Messaging for Frappe WhatsApp
# bulk_whatsapp_messaging.py

import frappe
from frappe import _
import json
import time
import random
import requests
from frappe.utils import cint, get_datetime, now
from frappe.model.document import Document
from frappe.model.naming import make_autoname

# Add these files to your frappe_whatsapp app

# 1. First, create a new DocType for Bulk WhatsApp Messaging
# Save this as a Python file in your app's folder: 
# frappe_whatsapp/frappe_whatsapp/doctype/bulk_whatsapp_message/bulk_whatsapp_message.py

class BulkWhatsAppMessage(Document):
    def autoname(self):
        self.name = make_autoname("BULK-WA-.YYYY.-.#####")
    
    def validate(self):
        # self.validate_message()
        self.validate_recipients()
        self.validate_attachment()

    def validate_message(self):
        if not self.message_content:
            frappe.throw(_("Message content is required"))

    def validate_attachment(self):
        """Block over-sized media before submit so the send doesn't silently fail.

        WhatsApp rejects large media (videos over ~16 MB) even when Evolution
        accepts the request and reports success, which otherwise leaves the
        campaign marked "Completed" while nothing is delivered.
        """
        if not self.attach:
            return
        max_mb = frappe.db.get_single_value("WhatsApp Settings", "max_attachment_size") or 16
        file_size = frappe.db.get_value("File", {"file_url": self.attach}, "file_size")
        if file_size and file_size > max_mb * 1024 * 1024:
            frappe.throw(
                _(
                    "The attachment is {0} MB, over the {1} MB limit. WhatsApp rejects "
                    "large media (videos above ~16 MB) even if the send looks successful. "
                    "Use a smaller file."
                ).format(round(file_size / (1024 * 1024), 1), max_mb),
                title=_("Attachment Too Large"),
            )
    
    def validate_recipients(self):
        if not self.recipients and not self.recipient_list:
            frappe.throw(_("At least one recipient or a recipient list is required"))
        
        # If recipient list is provided, count recipients
        if self.recipient_type == 'Recipient List' and self.recipient_list:
            recipient_count = frappe.db.count("WhatsApp Recipient", {"parent": self.recipient_list})
            if recipient_count == 0:
                frappe.throw(_("Selected recipient list has no recipients"))
            self.recipient_count = recipient_count
        # If individual recipients are provided
        elif self.recipients:
            self.recipient_count = len(self.recipients)
    
    def on_submit(self):
        self.db_set("status", "In Progress")
        self.send_messages()
    
    def _resolve_sender(self):
        """Resolve (base_url, api_key, instance_name) of the sending instance.

        Uses the selected 'Send From Instance', falling back to the current
        user's own WhatsApp instance. Cached on the doc for the whole run.
        """
        from frappe_whatsapp.api import _server_credentials

        instance_name = self.sender_number or frappe.db.get_value(
            "Whatsapp Instance", {"linked_user": frappe.session.user}, "name"
        )
        if not instance_name:
            frappe.throw(_("No WhatsApp instance to send from. Set 'Send From Instance'."))

        server = frappe.db.get_value("Whatsapp Instance", instance_name, "evolution_server")
        base_url, api_key = _server_credentials(server)

        self._sender_base = base_url
        self._sender_key = api_key
        self._sender_instance = instance_name

    def send_messages(self):
        """Send messages directly (synchronously) with progress updates"""
        # Resolve the sending instance up-front (aborts the run if unavailable).
        self._resolve_sender()
        self._send_errors = []

        recipients_list = self._gather_recipients()

        total = len(recipients_list)
        sent = 0
        failed = 0
        
        for i, recipient in enumerate(recipients_list):
            # Show progress to user
            frappe.publish_progress(
                percent=int((i / total) * 100),
                title=_("Sending WhatsApp Messages"),
                description=_("Sending message {0} of {1} to {2}").format(
                    i + 1, total, recipient.get("recipient_name") or recipient.get("mobile_number")
                )
            )
            
            success = self.send_single_message(recipient)
            if success:
                sent += 1
            else:
                failed += 1
        
        # Final progress update
        frappe.publish_progress(
            percent=100,
            title=_("Sending WhatsApp Messages"),
            description=_("Completed: {0} sent, {1} failed").format(sent, failed)
        )
        
        # Update final status
        if failed == 0:
            self.db_set("status", "Completed")
        elif sent == 0:
            self.db_set("status", "Failed")
        else:
            self.db_set("status", "Partially Failed")
        
        msg = _("Bulk WhatsApp Message completed: {0} sent, {1} failed").format(sent, failed)
        if getattr(self, "_send_errors", None):
            msg += "<br><br><b>" + _("Last error") + ":</b> " + frappe.utils.escape_html(
                self._send_errors[-1]
            )
        frappe.msgprint(
            msg,
            indicator="green" if failed == 0 else "orange",
            title=_("Bulk Send Result"),
        )
    
    def _build_message_text(self, recipient):
        """Resolve the final message text for one recipient.

        Returns the template body with its {{1}}, {{2}}... placeholders filled
        (from the recipient's own data for Unique, or the shared Template
        Variables for Common), or the plain Message Content. Returns None when a
        template is configured but cannot be found.
        """
        if self.use_template and self.template:
            template = frappe.db.get_value("WhatsApp Templates", self.template, fieldname="*")
            if not template:
                frappe.log_error(f"Template {self.template} not found", "WhatsApp Bulk Messaging")
                return None

            parameters = []
            if recipient.get("recipient_data") and self.variable_type == "Unique":
                try:
                    parameters = list(json.loads(recipient.get("recipient_data", "{}")).values())
                except Exception:
                    pass
            elif self.template_variables and self.variable_type == "Common":
                try:
                    parameters = list(json.loads(self.template_variables).values())
                except Exception:
                    pass

            message_text = (
                template.get("template")
                or template.get("message_content")
                or template.get("body")
                or template.get("message")
                or ""
            )
            for i, param in enumerate(parameters, 1):
                message_text = message_text.replace(f"{{{{{i}}}}}", str(param))
            return message_text

        return self.message_content or ""

    def _gather_recipients(self):
        """Return the recipient list (mobile_number, recipient_name, recipient_data)."""
        if self.recipient_type == "Recipient List" and self.recipient_list:
            return frappe.get_all(
                "WhatsApp Recipient",
                filters={"parent": self.recipient_list},
                fields=["mobile_number", "name", "recipient_name", "recipient_data"],
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
        """Return the resolved message for the first ``limit`` recipients (no send)."""
        limit = int(limit or 25)
        out = []
        for r in self._gather_recipients()[:limit]:
            text = self._build_message_text(r)
            out.append(
                {
                    "mobile": r.get("mobile_number"),
                    "name": r.get("recipient_name"),
                    "message": text if text is not None else _("(template not found)"),
                }
            )
        return out

    def send_single_message(self, recipient):
        """Send a single message via Mubtkir API. Returns True on success, False on failure."""
        
        # Add random delay between messages to prevent blocking
        min_delay = cint(self.message_delay) or 20
        max_delay = cint(self.max_delay) or 60
        if max_delay < min_delay:
            max_delay = min_delay
        delay = random.randint(min_delay, max_delay)
        time.sleep(delay)
        
        # Get phone number
        phone_number = recipient.get("mobile_number")
        if not phone_number:
            frappe.log_error("No phone number for recipient", "WhatsApp Bulk Messaging")
            return False
        
        # Format phone number
        phone_number = self.format_number(phone_number)
        
        # Parse recipient data for template variables
        recipient_data = {}
        if recipient.get("recipient_data"):
            try:
                recipient_data = json.loads(recipient.get("recipient_data", "{}"))
            except Exception as e:
                frappe.log_error(f"Error parsing recipient data: {str(e)}", "WhatsApp Bulk Messaging")
        
        # The sending instance was resolved once in send_messages(); guard in
        # case send_single_message is ever called on its own.
        if not getattr(self, "_sender_instance", None):
            self._resolve_sender()

        headers = {
            "Content-Type": "application/json",
            "apikey": self._sender_key,
        }
        
        success = False
        response_data = None
        error_message = None
        message_text = None
        
        try:
            # 1) Resolve the message text — from a template, or the plain body.
            message_text = self._build_message_text(recipient)
            if message_text is None:
                # Template configured but not found (already logged) — skip.
                return

            # 2) Resolve the attachment URL (device upload or ERPNext file).
            attachment_url = None
            filename = None
            if self.attach:
                filename = self.attach.split("/")[-1] if "/" in self.attach else self.attach
                if self.attach.startswith(("http://", "https://")):
                    attachment_url = self.attach
                else:
                    from urllib.parse import quote

                    path = self.attach if self.attach.startswith("/") else "/" + self.attach
                    # Percent-encode so Arabic names / spaces in the path are valid
                    # for the Evolution server to fetch (keep the path slashes).
                    attachment_url = frappe.utils.get_url() + quote(path, safe="/")

            # 3) Build the payload — media (image / PDF / video / audio) or text.
            if attachment_url:
                mediatype, mimetype = _media_kind(filename)
                url = f"{self._sender_base}/message/sendMedia/{self._sender_instance}"
                payload = {
                    "number": phone_number,
                    "mediatype": mediatype,
                    "media": attachment_url,
                }
                if message_text:
                    payload["caption"] = message_text
                if mediatype == "document":
                    payload["fileName"] = filename
                    payload["mimetype"] = mimetype or "application/octet-stream"
                content_type = mediatype
            else:
                if not message_text:
                    message_text = "No Text"
                url = f"{self._sender_base}/message/sendText/{self._sender_instance}"
                payload = {"number": phone_number, "text": message_text}
                content_type = "text"

            # Make request to Mubtkir API
            response = requests.post(url, headers=headers, json=payload, timeout=30)
            response_data = response.json()
            
            if response.status_code in [200, 201]:
                success = True
                
                # Extract message ID from response
                message_id = response_data.get("key", {}).get("id", "")
                if not message_id:
                    message_id = response_data.get("message", {}).get("key", {}).get("id", "")
                
                # Create WhatsApp Message record for tracking
                new_doc = {
                    "doctype": "WhatsApp Message",
                    "type": "Outgoing",
                    "message": message_text,
                    "to": phone_number,
                    "message_type": "Template" if self.use_template else "Manual",
                    "message_id": message_id,
                    "content_type": content_type,
                    "status": "Sent",
                    "bulk_message_reference": self.name
                }

                if self.use_template:
                    new_doc.update({
                        "use_template": 1,
                        "template": self.template,
                        "template_parameters": recipient.get("recipient_data") if self.variable_type == 'Unique' else self.template_variables
                    })

                # Already sent via Evolution — don't let the doctype re-send via Meta.
                msg_doc = frappe.get_doc(new_doc)
                msg_doc.flags.skip_meta_send = True
                msg_doc.insert(ignore_permissions=True)
            else:
                self._record_failure(phone_number, message_text, _extract_error(response_data))

        except requests.exceptions.RequestException as e:
            self._record_failure(phone_number, message_text, f"Connection error: {e}")
        except Exception as e:
            self._record_failure(phone_number, message_text, str(e))
        finally:
            # Update sent count
            self.db_set("sent_count", cint(self.sent_count) + 1)

        return success

    def _record_failure(self, phone_number, message_text, error_message):
        """Log a send failure, keep it for the result popup, and store it on a
        Failed WhatsApp Message so the reason is visible on the record."""
        error_message = (error_message or "Unknown error")[:500]
        if not hasattr(self, "_send_errors"):
            self._send_errors = []
        self._send_errors.append(error_message)
        frappe.log_error(
            f"Failed to send WhatsApp message to {phone_number}: {error_message}",
            "WhatsApp Bulk Messaging",
        )
        try:
            fail_doc = frappe.get_doc(
                {
                    "doctype": "WhatsApp Message",
                    "type": "Outgoing",
                    "message": f"{message_text or ''}\n\n[ERROR] {error_message}".strip(),
                    "to": phone_number,
                    "message_type": "Template" if self.use_template else "Manual",
                    "status": "Failed",
                    "bulk_message_reference": self.name,
                }
            )
            fail_doc.flags.skip_meta_send = True
            fail_doc.insert(ignore_permissions=True)
        except Exception:
            pass
    
    def format_number(self, number):
        """Normalise a phone number to WhatsApp's international digits-only form.

        Strips spaces, dashes, parentheses and a leading ``+``/``00``. As a
        convenience for Saudi numbers, a local ``05XXXXXXXX`` (10 digits) is
        converted to ``9665XXXXXXXX``. Numbers must otherwise already include
        their country code.
        """
        if not number:
            return number
        import re

        digits = re.sub(r"\D", "", str(number))
        if digits.startswith("00"):
            digits = digits[2:]
        # Local Saudi mobile (05XXXXXXXX) -> 9665XXXXXXXX.
        if len(digits) == 10 and digits.startswith("05"):
            digits = "966" + digits[1:]
        return digits

    def retry_failed(self):
        """Retry failed messages"""
        failed_messages = frappe.get_all(
            "WhatsApp Message",
            filters={
                "bulk_message_reference": self.name,
                "status": "Failed"
            },
            fields=["name"]
        )
        
        count = 0
        for msg in failed_messages:
            message_doc = frappe.get_doc("WhatsApp Message", msg.name)
            message_doc.status = "Queued"
            message_doc.save(ignore_permissions=True)
            count += 1
        
        frappe.msgprint(_("{0} messages have been requeued for sending").format(count))
        
    def get_progress(self):
        """Get sending progress for this bulk message"""
        total = self.recipient_count
        sent = frappe.db.count("WhatsApp Message", {
            "bulk_message_reference": self.name,
            "status": ["in", ["sent","delivered", "Success", "read"]]
        })
        failed = frappe.db.count("WhatsApp Message", {
            "bulk_message_reference": self.name,
            "status": "Failed"
        })
        queued = frappe.db.count("WhatsApp Message", {
            "bulk_message_reference": self.name,
            "status": "Queued"
        })
        
        return {
            "total": total,
            "sent": sent,
            "failed": failed,
            "queued": queued,
            "percent": (sent / total * 100) if total else 0
        }


# Map a file extension to an Evolution ``mediatype`` and (for documents) a mime.
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


def _extract_error(response_data):
    """Pull a human-readable error string out of Evolution's varied error shapes."""
    if isinstance(response_data, dict):
        resp = response_data.get("response")
        if isinstance(resp, dict):
            msg = resp.get("message")
            if isinstance(msg, list):
                return ", ".join(str(m) for m in msg)
            if msg:
                return str(msg)
        return str(response_data.get("message") or response_data)
    return str(response_data)


def _media_kind(filename):
    """Return ``(mediatype, mimetype)`` for a filename.

    ``mediatype`` is one of image / video / audio / document (Evolution's
    sendMedia types). ``mimetype`` is only meaningful for documents; it is
    ``None`` for image/video/audio.
    """
    ext = filename.rsplit(".", 1)[-1].lower() if filename and "." in filename else ""
    for kind, exts in _MEDIA_EXT.items():
        if ext in exts:
            return kind, None
    return "document", _DOC_MIME.get(ext)
