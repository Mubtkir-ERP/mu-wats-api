# Copyright (c) 2022, Shridhar Patil and contributors
# For license information, please see license.txt
import json
import frappe
from frappe.model.document import Document
from frappe.integrations.utils import make_post_request


class WhatsAppMessage(Document):
    """Send whats app messages."""

    def before_insert(self):
        """Send message.

        Routing: an Outgoing message is sent through Evolution when channel ==
        "Evolution"; otherwise it takes the original Meta Cloud API path
        (_send_via_meta) unchanged. Records already sent by the Evolution/bulk
        flow set skip_meta_send and are never re-sent here.
        """
        if self.flags.get("skip_meta_send"):
            return

        if self.type == "Outgoing" and (self.channel or "Meta") == "Evolution":
            self._send_via_evolution()
            return

        self._send_via_meta()

    def _send_via_evolution(self):
        """Send an Outgoing message through the unified Evolution layer.

        Supports text, media (via attach) and interactive buttons/list
        (via interactive_payload JSON). Sets status + message_id on success.
        """
        from frappe_whatsapp.utils import evolution

        instance = self.send_from_instance or None

        # Interactive payload takes precedence when present.
        payload = None
        if self.get("interactive_payload"):
            try:
                payload = frappe.parse_json(self.interactive_payload)
            except Exception:
                payload = None

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
                if link and not link.startswith("http"):
                    link = frappe.utils.get_url() + "/" + link.lstrip("/")
                mediatype = self.content_type if self.content_type in ("image", "video", "audio", "document") else "image"
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
                self.message_id = evolution.send_text(
                    number=self.to,
                    text=self.message,
                    instance_name=instance,
                )
            self.status = "Sent"
        except Exception as e:
            self.status = "Failed"
            frappe.throw(f"Failed to send via Evolution: {str(e)}")

    def _send_via_meta(self):
        """Original Meta Cloud API send path (reference; unchanged behaviour)."""
        if self.type == "Outgoing" and self.message_type != "Template":
            if self.attach and not self.attach.startswith("http"):
                link = frappe.utils.get_url() + "/" + self.attach
            else:
                link = self.attach

            data = {
                "messaging_product": "whatsapp",
                "to": self.format_number(self.to),
                "type": self.content_type,
            }
            if self.is_reply and self.reply_to_message_id:
                data["context"] = {"message_id": self.reply_to_message_id}
            if self.content_type in ["document", "image", "video"]:
                data[self.content_type.lower()] = {
                    "link": link,
                    "caption": self.message,
                }
            elif self.content_type == "reaction":
                data["reaction"] = {
                    "message_id": self.reply_to_message_id,
                    "emoji": self.message,
                }
            elif self.content_type == "text":
                data["text"] = {"preview_url": True, "body": self.message}

            elif self.content_type == "audio":
                data["text"] = {"link": link}

            try:
                self.notify(data)
                self.status = "Sent"
            except Exception as e:
                self.status = "Failed"
                frappe.throw(f"Failed to send message {str(e)}")
        elif self.type == "Outgoing" and self.message_type == "Template" and not self.message_id and not self.flags.get("skip_meta_send"):
            # Only send via Meta if a WhatsApp Settings token is configured.
            # Messages created by the Evolution flow set skip_meta_send and
            # already have their content sent, so we must not re-send here.
            if frappe.db.get_single_value("WhatsApp Settings", "token"):
                self.send_template()

    def send_template(self):
        """Send template."""
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

        if template.sample_values:
            field_names = template.field_names.split(",") if template.field_names else template.sample_values.split(",")
            parameters = []
            template_parameters = []

            if self.body_param is not None:
                params = list(json.loads(self.body_param).values())
                for param in params:
                    parameters.append({"type": "text", "text": param})
                    template_parameters.append(param)
            elif self.flags.custom_ref_doc:
                custom_values = self.flags.custom_ref_doc
                for field_name in field_names:
                    value = custom_values.get(field_name.strip())
                    parameters.append({"type": "text", "text": value})
                    template_parameters.append(value)                    

            else:
                ref_doc = frappe.get_doc(self.reference_doctype, self.reference_name)
                for field_name in field_names:
                    value = ref_doc.get_formatted(field_name.strip())
                    parameters.append({"type": "text", "text": value})
                    template_parameters.append(value)

            self.template_parameters = json.dumps(template_parameters)
            data["template"]["components"].append(
                {
                    "type": "body",
                    "parameters": parameters,
                }
            )

        if template.header_type:
            if self.attach:
                if template.header_type == 'IMAGE':

                    if self.attach.startswith("http"):
                        url = f'{self.attach}'
                    else:
                        url = f'{frappe.utils.get_url()}{self.attach}'
                    data['template']['components'].append({
                        "type": "header",
                        "parameters": [{
                            "type": "image",
                            "image": {
                                "link": url
                            }
                        }]
                    })

            elif template.sample:
                if template.header_type == 'IMAGE':
                    if template.sample.startswith("http"):
                        url = f'{template.sample}'
                    else:
                        url = f'{frappe.utils.get_url()}{template.sample}'
                    data['template']['components'].append({
                        "type": "header",
                        "parameters": [{
                            "type": "image",
                            "image": {
                                "link": url
                            }
                        }]
                    })

        self.notify(data)

    def notify(self, data):
        """Notify."""
        settings = frappe.get_doc(
            "WhatsApp Settings",
            "WhatsApp Settings",
        )
        token = settings.get_password("token")

        headers = {
            "authorization": f"Bearer {token}",
            "content-type": "application/json",
        }
        try:
            response = make_post_request(
                f"{settings.url}/{settings.version}/{settings.phone_id}/messages",
                headers=headers,
                data=json.dumps(data),
            )
            self.message_id = response["messages"][0]["id"]

        except Exception as e:
            res = frappe.flags.integration_request.json()["error"]
            error_message = res.get("Error", res.get("message"))
            frappe.get_doc(
                {
                    "doctype": "WhatsApp Notification Log",
                    "template": "Text Message",
                    "meta_data": frappe.flags.integration_request.json(),
                }
            ).insert(ignore_permissions=True)

            frappe.throw(msg=error_message, title=res.get("error_user_title", "Error"))

    def format_number(self, number):
        """Format number."""
        if number.startswith("+"):
            number = number[1 : len(number)]

        return number

    @frappe.whitelist()
    def send_read_receipt(self):
        data = {
            "messaging_product": "whatsapp",
            "status": "read",
            "message_id": self.message_id
        }

        settings = frappe.get_doc(
            "WhatsApp Settings",
            "WhatsApp Settings",
        )

        token = settings.get_password("token")

        headers = {
            "authorization": f"Bearer {token}",
            "content-type": "application/json",
        }
        try:
            response = make_post_request(
                f"{settings.url}/{settings.version}/{settings.phone_id}/messages",
                headers=headers,
                data=json.dumps(data),
            )

            if response.get("success"):
                self.status = "marked as read"
                self.save()
                return response.get("success")

        except Exception as e:
            res = frappe.flags.integration_request.json()["error"]
            error_message = res.get("Error", res.get("message"))
            frappe.log_error("WhatsApp API Error", f"{error_message}\n{res}")

def on_doctype_update():
    frappe.db.add_index("WhatsApp Message", ["reference_doctype", "reference_name"])


@frappe.whitelist()
def send_template(to, reference_doctype, reference_name, template):
    try:
        doc = frappe.get_doc({
            "doctype": "WhatsApp Message",
            "to": to,
            "type": "Outgoing",
            "message_type": "Template",
            "reference_doctype": reference_doctype,
            "reference_name": reference_name,
            "content_type": "text",
            "template": template
        })

        doc.save()
    except Exception as e:
        raise e
