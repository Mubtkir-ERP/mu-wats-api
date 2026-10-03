# Copyright (c) 2022, Shridhar Patil and contributors
# For license information, please see license.txt

import json

import frappe
from frappe import _
from frappe.integrations.utils import make_post_request
from frappe.model.document import Document

from frappe_whatsapp.utils.messaging import (
    ensure_enabled,
    normalize_number,
    resolve_channel,
    resolve_instance_name,
)


class WhatsAppMessage(Document):
    """One inbound or outbound WhatsApp message.

    Every outbound path is routed through the message's explicit ``channel``;
    when it is blank, WhatsApp Settings -> Default Channel is used.
    """

    def before_insert(self):
        if self.type == "Incoming":
            self.status = self.status or "Received"
            self.channel = self.get("channel") or "Meta"
            return

        if self.type != "Outgoing":
            return

        # Records created after a caller has already sent the payload (bulk,
        # webhook migration, etc.) are log-only and must never send twice.
        if self.flags.get("skip_meta_send"):
            if not self.channel:
                self.channel = resolve_channel()
            return

        ensure_enabled()
        self.to = normalize_number(self.to)
        if not self.to:
            frappe.throw(_("Recipient mobile number is required."))

        self.channel = resolve_channel(self.get("channel"))
        self.status = self.status or "Pending"

        if self.channel == "Evolution":
            self.send_from_instance = resolve_instance_name(
                self.get("send_from_instance"), user=self.owner or frappe.session.user
            )
            if self.message_type == "Template" and not self.message:
                self.message = self._render_evolution_template()
            self._send_via_evolution()
        else:
            self._send_via_meta()

    # ------------------------------------------------------------------
    # Evolution
    # ------------------------------------------------------------------
    def _template_parameter_values(self, template):
        """Resolve template body parameters in a deterministic order."""
        if self.body_param:
            try:
                parsed = json.loads(self.body_param)
                return list(parsed.values()) if isinstance(parsed, dict) else list(parsed)
            except Exception:
                pass

        if self.template_parameters:
            try:
                parsed = json.loads(self.template_parameters)
                return list(parsed.values()) if isinstance(parsed, dict) else list(parsed)
            except Exception:
                pass

        field_names = []
        if template.field_names:
            field_names = [x.strip() for x in template.field_names.split(",") if x.strip()]
        elif template.sample_values:
            # Keep the same parameter count when a template has samples but no
            # field map. Without a reference mapping these become empty values.
            field_names = [x.strip() for x in template.sample_values.split(",") if x.strip()]

        if self.flags.get("custom_ref_doc"):
            values = self.flags.custom_ref_doc
            return [values.get(name, "") for name in field_names]

        if field_names and self.reference_doctype and self.reference_name:
            ref_doc = frappe.get_doc(self.reference_doctype, self.reference_name)
            out = []
            for name in field_names:
                try:
                    out.append(ref_doc.get_formatted(name))
                except Exception:
                    out.append(ref_doc.get(name) or "")
            return out

        return []

    def _render_evolution_template(self):
        if not self.template:
            frappe.throw(_("A WhatsApp Template is required."))
        template = frappe.get_doc("WhatsApp Templates", self.template)
        text = template.template or ""
        values = self._template_parameter_values(template)
        for idx, value in enumerate(values, 1):
            text = text.replace("{{%s}}" % idx, str(value or ""))
        self.template_parameters = json.dumps(values, ensure_ascii=False, default=str)
        if not str(text).strip():
            frappe.throw(_("The selected WhatsApp Template has no message body."))
        return text

    def _send_via_evolution(self):
        from frappe_whatsapp.utils import evolution

        instance = self.send_from_instance
        payload = None
        if self.get("interactive_payload"):
            try:
                payload = frappe.parse_json(self.interactive_payload)
            except Exception:
                frappe.throw(_("Interactive Payload must be valid JSON."))

        try:
            if payload and payload.get("buttons"):
                self.message_id = evolution.send_buttons(
                    number=self.to,
                    title=payload.get("title", ""),
                    description=payload.get("description") or self.message or "",
                    buttons=payload.get("buttons", []),
                    footer=payload.get("footer"),
                    instance_name=instance,
                )
                self.content_type = "button"
            elif payload and payload.get("sections"):
                self.message_id = evolution.send_list(
                    number=self.to,
                    title=payload.get("title", ""),
                    description=payload.get("description") or self.message or "",
                    button_text=payload.get("button_text") or payload.get("buttonText") or "Select",
                    sections=payload.get("sections", []),
                    footer=payload.get("footer"),
                    instance_name=instance,
                )
                self.content_type = "flow"
            elif self.attach:
                link = self.attach
                if link and not link.startswith(("http://", "https://")):
                    link = frappe.utils.get_url() + "/" + link.lstrip("/")
                mediatype = (
                    self.content_type
                    if self.content_type in ("image", "video", "audio", "document")
                    else "document"
                )
                filename = self.attach.split("/")[-1] if "/" in self.attach else self.attach
                self.message_id = evolution.send_media(
                    number=self.to,
                    media_url=link,
                    mediatype=mediatype,
                    caption=self.message,
                    filename=filename,
                    instance_name=instance,
                )
            else:
                self.content_type = self.content_type or "text"
                self.message_id = evolution.send_text(
                    number=self.to,
                    text=self.message,
                    instance_name=instance,
                )
            self.status = "Sent"
            self.failure_reason = ""
        except Exception as exc:
            self.status = "Failed"
            self.failure_reason = str(exc)[:1000]
            frappe.throw(
                _("Failed to send via Evolution: {0}").format(str(exc)),
                title=_("WhatsApp Send Failed"),
            )

    # ------------------------------------------------------------------
    # Meta Cloud API
    # ------------------------------------------------------------------
    def _meta_settings(self):
        settings = frappe.get_doc("WhatsApp Settings", "WhatsApp Settings")
        try:
            token = settings.get_password("token", raise_exception=False)
        except TypeError:
            # Compatibility with older Frappe get_password signatures.
            try:
                token = settings.get_password("token")
            except Exception:
                token = None
        except Exception:
            token = None

        if not token:
            frappe.throw(
                _(
                    "Meta is the selected WhatsApp channel but no valid Meta token is configured. "
                    "Add the token or change Default Channel to Evolution in WhatsApp Settings."
                ),
                title=_("Meta Token Missing"),
            )
        if not settings.url or not settings.version or not settings.phone_id:
            frappe.throw(
                _("Meta URL, Version and Phone ID are required in WhatsApp Settings."),
                title=_("Meta Settings Incomplete"),
            )
        return settings, token

    def _send_via_meta(self):
        try:
            if self.message_type == "Template":
                if not self.message_id:
                    self.send_template()
                self.status = "Sent"
                self.failure_reason = ""
                return

            link = self.attach
            if link and not link.startswith(("http://", "https://")):
                link = frappe.utils.get_url() + "/" + link.lstrip("/")

            data = {
                "messaging_product": "whatsapp",
                "to": self.format_number(self.to),
                "type": self.content_type or "text",
            }
            if self.is_reply and self.reply_to_message_id:
                data["context"] = {"message_id": self.reply_to_message_id}

            if self.content_type in ("document", "image", "video", "audio"):
                if not link:
                    frappe.throw(_("An attachment is required for {0} messages.").format(self.content_type))
                data[self.content_type] = {"link": link}
                if self.message and self.content_type != "audio":
                    data[self.content_type]["caption"] = self.message
            elif self.content_type == "reaction":
                data["reaction"] = {
                    "message_id": self.reply_to_message_id,
                    "emoji": self.message,
                }
            elif (self.content_type or "text") == "text":
                if not self.message:
                    frappe.throw(_("Message text is required."))
                data["text"] = {"preview_url": True, "body": self.message}
            else:
                frappe.throw(_("Meta sending for content type {0} is not supported.").format(self.content_type))

            self.notify(data)
            self.status = "Sent"
            self.failure_reason = ""
        except Exception as exc:
            self.status = "Failed"
            self.failure_reason = str(exc)[:1000]
            raise

    def send_template(self):
        if not self.template:
            frappe.throw(_("A WhatsApp Template is required."))
        template = frappe.get_doc("WhatsApp Templates", self.template)
        data = {
            "messaging_product": "whatsapp",
            "to": self.format_number(self.to),
            "type": "template",
            "template": {
                "name": template.actual_name or template.template_name,
                "language": {"code": template.language_code},
                "components": [],
            },
        }

        values = self._template_parameter_values(template)
        if values:
            parameters = [{"type": "text", "text": str(value or "")} for value in values]
            self.template_parameters = json.dumps(values, ensure_ascii=False, default=str)
            data["template"]["components"].append({"type": "body", "parameters": parameters})

        if template.header_type == "IMAGE":
            source = self.attach or template.sample
            if source:
                url = source if source.startswith(("http://", "https://")) else f"{frappe.utils.get_url()}{source}"
                data["template"]["components"].append(
                    {
                        "type": "header",
                        "parameters": [{"type": "image", "image": {"link": url}}],
                    }
                )
        elif template.header_type == "DOCUMENT" and self.attach:
            url = self.attach if self.attach.startswith(("http://", "https://")) else f"{frappe.utils.get_url()}{self.attach}"
            data["template"]["components"].append(
                {
                    "type": "header",
                    "parameters": [{"type": "document", "document": {"link": url}}],
                }
            )

        self.notify(data)

    def notify(self, data):
        settings, token = self._meta_settings()
        headers = {
            "authorization": f"Bearer {token}",
            "content-type": "application/json",
        }
        try:
            response = make_post_request(
                f"{settings.url.rstrip('/')}/{settings.version}/{settings.phone_id}/messages",
                headers=headers,
                data=json.dumps(data),
            )
            self.message_id = (response.get("messages") or [{}])[0].get("id")
            if not self.message_id:
                frappe.throw(_("Meta accepted the request but returned no message ID."))
        except Exception as exc:
            error_message = str(exc)
            meta_data = {"error": error_message}
            integration_request = getattr(frappe.flags, "integration_request", None)
            if integration_request:
                try:
                    payload = integration_request.json()
                    meta_data = payload
                    err = payload.get("error") if isinstance(payload, dict) else None
                    if isinstance(err, dict):
                        error_message = err.get("error_user_msg") or err.get("message") or error_message
                except Exception:
                    pass
            try:
                frappe.get_doc(
                    {
                        "doctype": "WhatsApp Notification Log",
                        "template": "Meta Send Error",
                        "meta_data": meta_data,
                    }
                ).insert(ignore_permissions=True)
            except Exception:
                pass
            frappe.throw(error_message, title=_("Meta WhatsApp Error"))

    def format_number(self, number):
        return normalize_number(number)

    @frappe.whitelist()
    def send_read_receipt(self):
        # Evolution read receipts are not sent by this Meta endpoint.
        if (self.get("channel") or "Meta") == "Evolution":
            return False
        if self.get("read_receipt_sent"):
            return True

        settings, token = self._meta_settings()
        data = {
            "messaging_product": "whatsapp",
            "status": "read",
            "message_id": self.message_id,
        }
        headers = {
            "authorization": f"Bearer {token}",
            "content-type": "application/json",
        }
        try:
            make_post_request(
                f"{settings.url.rstrip('/')}/{settings.version}/{settings.phone_id}/messages",
                headers=headers,
                data=json.dumps(data),
            )
            if self.meta.has_field("read_receipt_sent"):
                self.db_set("read_receipt_sent", 1, update_modified=False)
            return True
        except Exception:
            frappe.log_error(frappe.get_traceback(), "WhatsApp read receipt failed")
            return False


def on_doctype_update():
    frappe.db.add_index("WhatsApp Message", ["reference_doctype", "reference_name"])
    frappe.db.add_index("WhatsApp Message", ["message_id"])
    # ``channel`` is a Custom Field added by a patch. During a fresh install,
    # DocType sync can run before that patch creates the physical column.
    if frappe.db.has_column("WhatsApp Message", "channel"):
        frappe.db.add_index("WhatsApp Message", ["channel", "status"])


@frappe.whitelist()
def send_template(to, reference_doctype, reference_name, template):
    """Send a template from any ERPNext form through the configured default channel."""
    doc = frappe.get_doc(
        {
            "doctype": "WhatsApp Message",
            "to": normalize_number(to),
            "type": "Outgoing",
            "message_type": "Template",
            "reference_doctype": reference_doctype,
            "reference_name": reference_name,
            "content_type": "text",
            "template": template,
            # channel intentionally omitted: WhatsApp Settings decides.
        }
    )
    doc.insert(ignore_permissions=True)
    return {
        "name": doc.name,
        "status": doc.status,
        "channel": doc.get("channel"),
        "message_id": doc.message_id,
        "send_from_instance": doc.get("send_from_instance"),
    }
