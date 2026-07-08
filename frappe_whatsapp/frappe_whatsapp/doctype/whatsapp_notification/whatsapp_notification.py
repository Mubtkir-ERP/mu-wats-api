"""Notification."""

import base64
import json

import requests

import frappe

from frappe import _dict, _
from frappe.model.document import Document
from frappe.utils.safe_exec import get_safe_globals, safe_exec
from frappe.integrations.utils import make_post_request
from frappe.utils import add_to_date, nowdate, datetime


class WhatsAppNotification(Document):
    """Notification."""

    def validate(self):
        """Validate."""
        # Phone resolution is now handled by phone_source / phone_field, so the
        # old field_name existence check has been removed.
        if self.custom_attachment:
            if not self.attach and not self.attach_from_field:
                frappe.throw(_("Either {0} a file or add a {1} to send attachemt").format(
                    frappe.bold(_("Attach")),
                    frappe.bold(_("Attach from field")),
                ))

        if self.set_property_after_alert and self.reference_doctype:
            meta = frappe.get_meta(self.reference_doctype)
            if not meta.get_field(self.set_property_after_alert):
                frappe.throw(_("Field {0} not found on DocType {1}").format(
                    self.set_property_after_alert,
                    self.reference_doctype,
                ))


    def send_scheduled_message(self) -> dict:
        """Specific to API endpoint Server Scripts."""
        safe_exec(
            self.condition, get_safe_globals(), dict(doc=self)
        )

        template = frappe.db.get_value(
            "WhatsApp Templates", self.template,
            fieldname='*'
        )

        if template and template.language_code:
            if self.get("_contact_list"):
                # send simple template without a doc to get field data.
                self.send_simple_template(template)
            elif self.get("_data_list"):
                # allow send a dynamic template using schedule event config
                # _doc_list shoud be [{"name": "xxx", "phone_no": "123"}]
                for data in self._data_list:
                    doc = frappe.get_doc(self.reference_doctype, data.get("name"))

                    self.send_template_message(doc, data.get("phone_no"), template, True)
        # return _globals.frappe.flags


    def send_simple_template(self, template):
        """ send simple template without a doc to get field data """
        for contact in self._contact_list:
            data = {
                "messaging_product": "whatsapp",
                "to": self.format_number(contact),
                "type": "template",
                "template": {
                    "name": template.actual_name,
                    "language": {
                        "code": template.language_code
                    },
                    "components": []
                }
            }
            self.content_type = template.get("header_type", "text").lower()
            self.notify(data)


    def resolve_phone(self, doc, doc_data):
        """Resolve phone number based on phone_source setting."""
        phone = None
        source = self.phone_source or "Primary Contact"

        if source == "Primary Contact" or (self.phone_field and self.phone_field.startswith("primary_contact.")):
            # Get customer/party field
            customer = (doc_data.get('customer') or doc_data.get('party') or
                       doc_data.get('supplier') or doc_data.get('lead'))
            if customer:
                # Find party doctype
                party_doctype = None
                if doc_data.get('customer'):
                    party_doctype = "Customer"
                elif doc_data.get('supplier'):
                    party_doctype = "Supplier"

                if party_doctype:
                    # Get primary contact
                    contact_name = frappe.db.get_value(
                        "Dynamic Link",
                        {"link_doctype": party_doctype, "link_name": customer,
                         "parenttype": "Contact"},
                        "parent"
                    )
                    if contact_name:
                        contact = frappe.db.get_value(
                            "Contact", contact_name,
                            ["mobile_no", "phone"], as_dict=True
                        )
                        if contact:
                            phone = contact.mobile_no or contact.phone

            # Fallback to common fields
            if not phone:
                phone = (doc_data.get('contact_mobile') or
                        doc_data.get('mobile_no') or
                        doc_data.get('phone'))

        elif source == "Field in Document":
            if self.phone_field:
                phone = doc_data.get(self.phone_field)

        elif source == "Linked DocType":
            if self.phone_field and '.' in self.phone_field:
                parts = self.phone_field.split('.')
                link_field = parts[0]
                target_field = parts[1]
                linked_name = doc_data.get(link_field)
                if linked_name:
                    meta = frappe.get_meta(doc_data.get('doctype'))
                    df = meta.get_field(link_field)
                    if df and df.options:
                        phone = frappe.db.get_value(df.options, linked_name, target_field)

        return phone


    def send_template_message(self, doc: Document, phone_no=None, default_template=None, ignore_condition=False):
        """Send WhatsApp message using Evolution API instead of Meta."""
        if self.disabled:
            return

        doc_data = doc.as_dict()
        if self.condition and not ignore_condition:
            # check if condition satisfies
            if not frappe.safe_eval(
                self.condition, get_safe_globals(), dict(doc=doc_data)
            ):
                return

        # Build message text with template parameters
        template = default_template or frappe.db.get_value(
            "WhatsApp Templates", self.template,
            fieldname='*'
        )

        if not template:
            frappe.throw(f"Template {self.template} not found")

        # Get template message content
        message_text = self.code

        # Replace parameters in template
        if self.fields:
            parameters = []
            for field in self.fields:
                raw_value = None
                try:
                    if isinstance(doc, Document):
                        raw_value = doc.get(field.field_name)
                    else:
                        raw_value = doc_data.get(field.field_name)
                except Exception:
                    raw_value = None

                if raw_value is None:
                    raw_value = ""

                # Apply format
                fmt = field.get("field_format") or "Text"
                try:
                    if fmt == "Currency (SAR)":
                        value = f"{float(raw_value):,.2f} SAR" if raw_value else ""
                    elif fmt == "Date (DD/MM/YYYY)":
                        from frappe.utils import getdate
                        value = getdate(raw_value).strftime("%d/%m/%Y") if raw_value else ""
                    elif fmt == "Date (YYYY-MM-DD)":
                        from frappe.utils import getdate
                        value = str(getdate(raw_value)) if raw_value else ""
                    elif fmt == "Datetime":
                        value = str(raw_value)[:19] if raw_value else ""
                    elif fmt == "Number":
                        value = str(int(float(raw_value))) if raw_value else "0"
                    else:
                        value = str(raw_value) if raw_value else ""
                except Exception:
                    value = str(raw_value) or ""

                parameters.append(value)

            # Replace {{1}}, {{2}}, etc. with actual values
            for i, param in enumerate(parameters, 1):
                message_text = message_text.replace(f"{{{{{i}}}}}", _(str(param),'ar'))

        # Resolve the recipient phone number. An explicit phone_no (e.g. from a
        # scheduled _data_list entry) is used directly; otherwise resolve from
        # the phone_source / phone_field configuration.
        phone_number = phone_no or self.resolve_phone(doc, doc_data)

        if not phone_number:
            frappe.throw("Phone number not found")

        # Format phone number
        phone_number = self.format_number(phone_number)

        # Handle attachments
        attachment_url = None
        filename = None

        if self.attach_document_print:
            print_format = "Standard"
            doctype = frappe.get_doc("DocType", doc_data['doctype'])

            if doctype.custom and doctype.default_print_format:
                print_format = doctype.default_print_format
            elif self.print_format:
                print_format = self.print_format

            # Generate PDF using attach_print (handles permissions and PDF generation properly)
            try:
                pdf_data = frappe.attach_print(
                    doc_data['doctype'],
                    doc_data['name'],
                    print_format=print_format,
                    doc=doc
                )

                # Convert PDF to base64
                pdf_base64 = base64.b64encode(pdf_data["fcontent"]).decode('utf-8')

                filename = pdf_data["fname"]
                attachment_url = pdf_base64
            except Exception as e:
                error_msg = str(e)
                # Handle network/localhost errors with helpful message
                if "HostNotFoundError" in error_msg or "network error" in error_msg.lower():
                    frappe.throw(
                        _("PDF generation failed due to network error. Please ensure your site URL is properly configured in site_config.json (set 'host_name') or use a publicly accessible URL instead of localhost."),
                        title=_("PDF Generation Error")
                    )
                # Re-raise other errors
                raise

        elif self.custom_attachment:
            filename = self.file_name

            if self.attach_from_field:
                file_url = doc_data[self.attach_from_field]
                if not file_url.startswith("http"):
                    key = doc.get_document_share_key()
                    file_url = f'{frappe.utils.get_url()}{file_url}&key={key}'
            else:
                file_url = self.attach

            if file_url.startswith("http"):
                attachment_url = file_url
            else:
                attachment_url = f'{frappe.utils.get_url()}{file_url}'

        # Send message using Evolution API
        self.notify_evolution(
            phone_number=phone_number,
            message_text=message_text,
            attachment_url=attachment_url,
            filename=filename,
            template=template,
            doc_data=doc_data,
            parameters=parameters if self.fields else None
        )

    def notify_evolution(self, phone_number, message_text, attachment_url=None,
                         filename=None, template=None, doc_data=None, parameters=None):
        """Send message via Evolution API."""

        # Check if logged-in user has a linked Evolution Phone Settings
        user_evolution_settings = frappe.db.get_value(
            "Evolution Phone Settings",
            {"user": frappe.session.user},
            "name"
        )
        if user_evolution_settings:
            evolution_settings = frappe.get_doc("Evolution Phone Settings", user_evolution_settings)
        elif self.whatsapp_instance:
            # Check linked Whatsapp Instance (new field) and build a compatible
            # settings object from the instance + its Evolution Server.
           instance = frappe.get_doc("Whatsapp Instance", self.whatsapp_instance)
server = frappe.get_doc("Evolution Server", instance.evolution_server)
base_url = server.base_url.strip().rstrip('/')
if not base_url.startswith(('http://', 'https://')):
    base_url = 'https://' + base_url
evolution_settings = frappe._dict({
    "base_url": base_url,
    "instance_name": instance.instance_name,
    "global_api_key": server.get_password("api_key", raise_exception=False) or server.api_key,
})
        else:
            # WhatsApp Instance is mandatory, so this should never happen.
            frappe.throw(_("WhatsApp Instance is required to send messages."))

        if not evolution_settings.base_url or not evolution_settings.instance_name:
            frappe.throw("Evolution Phone Settings not configured")

        headers = {
            "Content-Type": "application/json",
            "apikey": evolution_settings.global_api_key
        }

        success = False
        response_data = None
        error_message = None

        try:
            # Determine content type and endpoint
            if attachment_url:
                # Check if it's a document (PDF) or image
                if filename and filename.lower().endswith('.pdf'):
                    # Send document
                    url = f"{evolution_settings.base_url}/message/sendMedia/{evolution_settings.instance_name}"
                    payload = {
                        "number": phone_number,
                        "mediatype": "document",
                        "mimetype": "application/pdf",
                        "caption": message_text,
                        "media": attachment_url,
                        "fileName": filename
                    }
                    content_type = 'document'
                else:
                    # Send image
                    url = f"{evolution_settings.base_url}/message/sendMedia/{evolution_settings.instance_name}"
                    payload = {
                        "number": phone_number,
                        "mediatype": "image",
                        "caption": message_text,
                        "media": attachment_url
                    }
                    content_type = 'image'
            else:
                if message_text is None:
                    message_text = 'No Text'
                # Send text message
                url = f"{evolution_settings.base_url}/message/sendText/{evolution_settings.instance_name}"
                payload = {
                    "number": phone_number,
                    "text": message_text
                }
                content_type = 'text'

            # Make request to Evolution API
            response = requests.post(url, headers=headers, json=payload, timeout=30)
            response_data = response.json()

            if response.status_code in [200, 201]:
                success = True

                # Extract message ID from response
                message_id = response_data.get("key", {}).get("id", "")
                if not message_id:
                    message_id = response_data.get("message", {}).get("key", {}).get("id", "")

                # Create WhatsApp Message record
                new_doc = {
                    "doctype": "WhatsApp Message",
                    "type": "Outgoing",
                    "status": "Sent",
                    "message": message_text,
                    "to": phone_number,
                    "message_type": "Template",
                    "message_id": message_id,
                    "content_type": content_type,
                    "use_template": 1,
                    "template": self.template,
                    "template_parameters": frappe.json.dumps(parameters, default=str) if parameters else None
                }

                if doc_data:
                    new_doc.update({
                        "reference_doctype": doc_data.get("doctype"),
                        "reference_name": doc_data.get("name"),
                    })

                frappe.get_doc(new_doc).save(ignore_permissions=True)

                # Update property after alert if configured
                if doc_data and self.set_property_after_alert and self.property_value:
                    if doc_data.get("doctype") and doc_data.get("name"):
                        fieldname = self.set_property_after_alert
                        value = self.property_value
                        meta = frappe.get_meta(doc_data.get("doctype"))
                        df = meta.get_field(fieldname)
                        if df:
                            if df.fieldtype in frappe.model.numeric_fieldtypes:
                                value = frappe.utils.cint(value)
                            frappe.db.set_value(
                                doc_data.get("doctype"),
                                doc_data.get("name"),
                                fieldname,
                                value
                            )

                frappe.msgprint("WhatsApp Message Sent Successfully", indicator="green", alert=True)
            else:
                success = False
                error_message = response_data.get("response", {}).get("message", "Unknown Error")

                # Create failed message record
                failed_doc = {
                    "doctype": "WhatsApp Message",
                    "type": "Outgoing",
                    "status": "Failed",
                    "message": message_text,
                    "to": phone_number,
                    "message_type": "Template",
                    "content_type": content_type,
                    "use_template": 1,
                    "template": self.template,
                }
                if doc_data:
                    failed_doc.update({
                        "reference_doctype": doc_data.get("doctype"),
                        "reference_name": doc_data.get("name"),
                    })
                frappe.get_doc(failed_doc).save(ignore_permissions=True)

                frappe.msgprint(
                    f"Failed to send WhatsApp message: {error_message}",
                    indicator="red",
                    alert=True
                )

        except requests.exceptions.RequestException as e:
            error_message = f"Connection error: {str(e)}"

            # Create failed message record
            failed_doc = {
                "doctype": "WhatsApp Message",
                "type": "Outgoing",
                "status": "Failed",
                "message": message_text,
                "to": phone_number,
                "message_type": "Template",
                "content_type": "text",
                "use_template": 1,
                "template": self.template,
            }
            if doc_data:
                failed_doc.update({
                    "reference_doctype": doc_data.get("doctype"),
                    "reference_name": doc_data.get("name"),
                })
            frappe.get_doc(failed_doc).save(ignore_permissions=True)

            frappe.msgprint(
                f"Failed to trigger WhatsApp message: {error_message}",
                indicator="red",
                alert=True
            )
        except Exception as e:
            error_message = str(e)

            # Create failed message record
            failed_doc = {
                "doctype": "WhatsApp Message",
                "type": "Outgoing",
                "status": "Failed",
                "message": message_text if message_text else "",
                "to": phone_number if phone_number else "",
                "message_type": "Template",
                "content_type": "text",
                "use_template": 1,
                "template": self.template,
            }
            if doc_data:
                failed_doc.update({
                    "reference_doctype": doc_data.get("doctype"),
                    "reference_name": doc_data.get("name"),
                })
            try:
                frappe.get_doc(failed_doc).save(ignore_permissions=True)
            except Exception:
                pass

            frappe.msgprint(
                f"Failed to trigger WhatsApp message: {error_message}",
                indicator="red",
                alert=True
            )
        finally:
            # Log the notification
            frappe.get_doc({
                "doctype": "WhatsApp Notification Log",
                "template": self.template,
                "meta_data": {
                    "success": success,
                    "response": response_data if success else None,
                    "error": error_message if not success else None,
                    "phone_number": phone_number,
                    "message": message_text
                }
            }).insert(ignore_permissions=True)

    def notify(self, data, doc_data=None):
        """Notify."""
        settings = frappe.get_doc(
            "WhatsApp Settings", "WhatsApp Settings",
        )
        token = settings.get_password("token")

        headers = {
            "authorization": f"Bearer {token}",
            "content-type": "application/json"
        }
        try:
            success = False
            response = make_post_request(
                f"{settings.url}/{settings.version}/{settings.phone_id}/messages",
                headers=headers, data=json.dumps(data)
            )

            if not self.get("content_type"):
                self.content_type = 'text'

            parameters = None
            if data["template"]["components"]:
                parameters = [param["text"] for param in data["template"]["components"][0]["parameters"]]
                parameters = frappe.json.dumps(parameters, default=str)

            new_doc = {
                "doctype": "WhatsApp Message",
                "type": "Outgoing",
                "status": "Sent",
                "message": str(data['template']),
                "to": data['to'],
                "message_type": "Template",
                "message_id": response['messages'][0]['id'],
                "content_type": self.content_type,
                "use_template": 1,
                "template": self.template,
                "template_parameters": parameters
            }

            if doc_data:
                new_doc.update({
                    "reference_doctype": doc_data.doctype,
                    "reference_name": doc_data.name,
                })

            frappe.get_doc(new_doc).save(ignore_permissions=True)

            if doc_data and self.set_property_after_alert and self.property_value:
                if doc_data.doctype and doc_data.name:
                    fieldname = self.set_property_after_alert
                    value = self.property_value
                    meta = frappe.get_meta(doc_data.get("doctype"))
                    df = meta.get_field(fieldname)
                    if df:
                        if df.fieldtype in frappe.model.numeric_fieldtypes:
                            value = frappe.utils.cint(value)

                        frappe.db.set_value(doc_data.get("doctype"), doc_data.get("name"), fieldname, value)

            frappe.msgprint("WhatsApp Message Triggered", indicator="green", alert=True)
            success = True

        except Exception as e:
            error_message = str(e)
            if frappe.flags.integration_request:
                response = frappe.flags.integration_request.json()['error']
                error_message = response.get('Error', response.get("message"))

            frappe.msgprint(
                f"Failed to trigger whatsapp message: {error_message}",
                indicator="red",
                alert=True
            )
        finally:
            if not success:
                meta = {"error": error_message}
            else:
                meta = frappe.flags.integration_request.json()
            frappe.get_doc({
                "doctype": "WhatsApp Notification Log",
                "template": self.template,
                "meta_data": meta
            }).insert(ignore_permissions=True)


    def on_trash(self):
        """On delete remove from schedule."""
        frappe.cache().delete_value("whatsapp_notification_map")


    def format_number(self, number):
        """Format number."""
        if (number.startswith("+")):
            number = number[1:len(number)]

        return number

    def get_documents_for_today(self):
        """get list of documents that will be triggered today"""
        docs = []

        diff_days = self.days_in_advance
        if self.doctype_event == "Days After":
            diff_days = -diff_days

        reference_date = add_to_date(nowdate(), days=diff_days)
        reference_date_start = reference_date + " 00:00:00.000000"
        reference_date_end = reference_date + " 23:59:59.000000"

        doc_list = frappe.get_all(
            self.reference_doctype,
            fields="name",
            filters=[
                {self.date_changed: (">=", reference_date_start)},
                {self.date_changed: ("<=", reference_date_end)},
            ],
        )

        for d in doc_list:
            doc = frappe.get_doc(self.reference_doctype, d.name)
            self.send_template_message(doc)
            # print(doc.name)


@frappe.whitelist()
def call_trigger_notifications():
    """Trigger notifications."""
    try:
        # Directly call the trigger_notifications function
        trigger_notifications()  
    except Exception as e:
        # Log the error but do not show any popup or alert
        frappe.log_error(frappe.get_traceback(), "Error in call_trigger_notifications")
        # Optionally, you could raise the exception to be handled elsewhere if needed
        raise e

def trigger_notifications(method="daily"):
    if frappe.flags.in_import or frappe.flags.in_patch:
        # don't send notifications while syncing or patching
        return

    if method == "daily":
        # KEEP existing Days Before/After logic unchanged
        doc_list = frappe.get_all(
            "WhatsApp Notification",
            filters={"doctype_event": ("in", ("Days Before", "Days After")), "disabled": 0}
        )
        for d in doc_list:
            alert = frappe.get_doc("WhatsApp Notification", d.name)
            alert.get_documents_for_today()


def trigger_monthly_notifications():
    """Triggered hourly — checks if today and current hour match schedule."""
    today = frappe.utils.getdate(frappe.utils.today())
    current_hour = frappe.utils.now_datetime().hour
    current_weekday = today.strftime("%A")  # "Monday", "Tuesday"...

    notifications = frappe.get_all(
        "WhatsApp Notification",
        filters={
            "notification_type": "Scheduler Event",
            "disabled": 0
        },
        fields=["name", "event_frequency", "schedule_day", "schedule_time",
                "week_day", "repeat_every"]
    )

    for n in notifications:
        try:
            freq = n.event_frequency
            repeat = n.repeat_every or 1
            scheduled_hour = int(str(n.schedule_time or "08:00:00")[:2]) if n.schedule_time else 8

            if scheduled_hour != current_hour:
                continue

            should_run = False

            if freq == "Daily":
                if today.day % repeat == 0:
                    should_run = True

            elif freq == "Weekly":
                if n.week_day and current_weekday == n.week_day:
                    week_number = today.isocalendar()[1]
                    if week_number % repeat == 0:
                        should_run = True

            elif freq == "Monthly":
                if n.schedule_day and today.day == n.schedule_day:
                    month_number = today.month
                    if month_number % repeat == 0:
                        should_run = True

            if should_run:
                alert = frappe.get_doc("WhatsApp Notification", n.name)
                alert.send_scheduled_message()

        except Exception:
            frappe.log_error(
                frappe.get_traceback(),
                f"WhatsApp Scheduled Notification Failed: {n.name}"
            )


@frappe.whitelist()
def get_preview(notification_name):
    """Return preview of message with real data from latest document."""
    notif = frappe.get_doc("WhatsApp Notification", notification_name)

    if not notif.reference_doctype:
        frappe.throw("No reference doctype selected")

    # Get the most recent document
    docs = frappe.get_all(
        notif.reference_doctype,
        fields=["name"],
        order_by="modified desc",
        limit=1
    )

    if not docs:
        frappe.throw(f"No documents found for {notif.reference_doctype}")

    doc = frappe.get_doc(notif.reference_doctype, docs[0].name)
    doc_data = doc.as_dict()

    message_text = notif.code or ""

    if notif.fields:
        for i, field in enumerate(notif.fields, 1):
            raw_value = doc_data.get(field.field_name)
            if raw_value is None:
                raw_value = ""

            fmt = field.get("field_format") or "Text"
            try:
                if fmt == "Currency (SAR)":
                    value = f"{float(raw_value):,.2f} SAR" if raw_value else ""
                elif fmt == "Date (DD/MM/YYYY)":
                    from frappe.utils import getdate
                    value = getdate(raw_value).strftime("%d/%m/%Y") if raw_value else ""
                elif fmt == "Number":
                    value = str(int(float(raw_value))) if raw_value else "0"
                else:
                    value = str(raw_value) if raw_value else ""
            except Exception:
                value = str(raw_value) if raw_value else ""

            message_text = message_text.replace(f"{{{{{i}}}}}", value)

    return {
        "preview": message_text,
        "doc_name": docs[0].name
    }
