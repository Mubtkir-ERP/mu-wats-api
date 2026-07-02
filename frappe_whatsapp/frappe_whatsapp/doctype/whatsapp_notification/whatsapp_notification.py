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
        # field_name has been replaced by the phone_source/phone_field columns
        # in the Print Format table, but legacy records may still carry it.
        if self.notification_type == "DocType Event" and self.reference_doctype and self.get("field_name"):
            fields = frappe.get_doc("DocType", self.reference_doctype).fields
            fields += frappe.get_all(
                "Custom Field",
                filters={"dt": self.reference_doctype},
                fields=["fieldname"]
            )
            if not any(field.fieldname == self.get("field_name") for field in fields): # noqa
                frappe.throw(_("Field name {0} does not exists").format(self.get("field_name")))
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


    def resolve_phone_number(self, doc, doc_data):
        """Resolve phone number(s) based on print_format_table configuration.

        Returns a list of (phone_number, row) tuples. A doctype configured on
        multiple rows resolves to multiple recipients.
        """
        if not self.print_format_table:
            frappe.throw("No document type configured in Print Format table")

        # Find matching rows for this doctype (could be multiple = multiple recipients)
        matched_rows = [
            row for row in self.print_format_table
            if row.document_type == doc_data.get('doctype')
        ]

        if not matched_rows:
            frappe.throw(f"No configuration found for {doc_data.get('doctype')} in Print Format table")

        phone_numbers = []

        for row in matched_rows:
            phone = None

            if row.phone_source == "Primary Contact":
                # Get customer/party from doc, then find primary contact mobile
                phone = self._get_primary_contact_phone(doc_data)
                if not phone:
                    # Fallback: try mobile_no or phone directly on doc
                    phone = doc_data.get('mobile_no') or doc_data.get('contact_mobile')

            elif row.phone_source == "Field in Document":
                # Direct field on the document
                phone = doc_data.get(row.phone_field)

            elif row.phone_source == "Linked DocType":
                # Follow link field chain e.g. "employee.cell_number"
                if row.phone_field and '.' in row.phone_field:
                    link_field, target_field = row.phone_field.split('.', 1)
                    linked_doc_name = doc_data.get(link_field)
                    if linked_doc_name:
                        # Get the meta to find what DocType this link points to
                        meta = frappe.get_meta(doc_data.get('doctype'))
                        df = meta.get_field(link_field)
                        if df and df.options:
                            phone = frappe.db.get_value(df.options, linked_doc_name, target_field)

            if phone:
                phone_numbers.append((phone, row))
            else:
                frappe.log_error(
                    f"Could not resolve phone number for {doc_data.get('name')} "
                    f"using source '{row.phone_source}' field '{row.phone_field}'",
                    "WhatsApp Phone Resolution"
                )

        if not phone_numbers:
            frappe.throw("Phone number not found")

        return phone_numbers  # list of (phone_number, row) tuples

    def _get_primary_contact_phone(self, doc_data):
        """Return the primary contact's mobile/phone for the document's party."""
        party = party_type = None
        if doc_data.get('customer'):
            party, party_type = doc_data.get('customer'), "Customer"
        elif doc_data.get('supplier'):
            party, party_type = doc_data.get('supplier'), "Supplier"
        elif doc_data.get('party'):
            party, party_type = doc_data.get('party'), doc_data.get('party_type')

        if not party or not party_type:
            return None

        # Query the Contact linked to this party, preferring the primary one.
        rows = frappe.db.sql(
            """
            SELECT c.mobile_no, c.phone
            FROM `tabContact` c
            INNER JOIN `tabDynamic Link` dl
                ON dl.parent = c.name AND dl.parenttype = 'Contact'
            WHERE dl.link_doctype = %s AND dl.link_name = %s
            ORDER BY c.is_primary_contact DESC, c.modified DESC
            LIMIT 1
            """,
            (party_type, party),
            as_dict=True,
        )
        if rows:
            return rows[0].get("mobile_no") or rows[0].get("phone")
        return None


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
        # message_text = template.get("message_content", "")

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

                # Apply fallback if empty
                if raw_value is None or raw_value == "":
                    raw_value = field.fallback_value or ""

                # Apply format
                fmt = field.get("field_format") or "Text"
                try:
                    if fmt == "Currency (SAR)":
                        value = f"{float(raw_value):,.2f} SAR" if raw_value else field.fallback_value or "0.00 SAR"
                    elif fmt == "Date (DD/MM/YYYY)":
                        from frappe.utils import getdate
                        value = getdate(raw_value).strftime("%d/%m/%Y") if raw_value else field.fallback_value or ""
                    elif fmt == "Date (YYYY-MM-DD)":
                        from frappe.utils import getdate
                        value = str(getdate(raw_value)) if raw_value else field.fallback_value or ""
                    elif fmt == "Datetime":
                        value = str(raw_value)[:19] if raw_value else field.fallback_value or ""
                    elif fmt == "Number":
                        value = str(int(float(raw_value))) if raw_value else field.fallback_value or "0"
                    else:
                        value = str(raw_value) if raw_value else field.fallback_value or ""
                except Exception:
                    value = field.fallback_value or str(raw_value) or ""

                parameters.append(value)

            # Replace {{1}}, {{2}}, etc. with actual values
            for i, param in enumerate(parameters, 1):
                message_text = message_text.replace(f"{{{{{i}}}}}", _(str(param),'ar'))

        # Resolve recipients. An explicit phone_no (e.g. from a scheduled
        # _data_list entry) is used directly; otherwise resolve from the Print
        # Format table configuration (which may yield multiple recipients).
        if phone_no:
            recipients = [(phone_no, None)]
        else:
            recipients = self.resolve_phone_number(doc, doc_data)

        for raw_phone, row in recipients:
            phone_number = self.format_number(raw_phone)

            # Handle attachments (per-recipient, since print format can vary by row)
            attachment_url = None
            filename = None

            if self.attach_document_print:
                print_format = "Standard"
                doctype = frappe.get_doc("DocType", doc_data['doctype'])

                if doctype.custom and doctype.default_print_format:
                    print_format = doctype.default_print_format
                else:
                    # Use the matched row's print format; if no row (explicit
                    # phone path) look it up from the table by doctype.
                    pf_row = row
                    if not pf_row and self.print_format_table:
                        pf_row = next(
                            (r for r in self.print_format_table
                             if r.document_type == doc_data.get('doctype')),
                            None,
                        )
                    if pf_row and pf_row.print_format:
                        print_format = pf_row.print_format

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
            evolution_settings = frappe._dict({
                "base_url": server.get_base_url(),
                "instance_name": instance.instance_name,
                "global_api_key": instance.get_password("api_key", raise_exception=False)
                or server.get_api_key(),
            })
        else:
            # Keep existing sender_number fallback as the final fallback (for
            # legacy records that still carry a sender_number value).
            sender = self.get("sender_number")
            if sender:
                evolution_settings = frappe.get_doc("Evolution Phone Settings", sender)
            else:
                frappe.throw(_("No WhatsApp Instance configured for this notification."))

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
                error_message = response_data.get("response", {}).get("message", "Unknown Error")
                frappe.msgprint(
                    f"Failed to send WhatsApp message: {error_message}",
                    indicator="red",
                    alert=True
                )

        except requests.exceptions.RequestException as e:
            error_message = f"Connection error: {str(e)}"
            frappe.msgprint(
                f"Failed to trigger WhatsApp message: {error_message}",
                indicator="red",
                alert=True
            )
        except Exception as e:
            error_message = str(e)
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
        # Existing Days Before/After logic - KEEP AS IS
        doc_list = frappe.get_all(
            "WhatsApp Notification", filters={"doctype_event": ("in", ("Days Before", "Days After")), "disabled": 0}
        )
        for d in doc_list:
            alert = frappe.get_doc("WhatsApp Notification", d.name)
            alert.get_documents_for_today()

    if method == "monthly":
        # New monthly scheduler
        today = frappe.utils.today()
        current_day = frappe.utils.getdate(today).day
        current_time = frappe.utils.now_datetime().strftime("%H:%M")

        monthly_notifications = frappe.get_all(
            "WhatsApp Notification",
            filters={
                "notification_type": "Scheduler Event",
                "event_frequency": "Monthly",
                "disabled": 0,
                "schedule_day": current_day
            }
        )

        for d in monthly_notifications:
            try:
                alert = frappe.get_doc("WhatsApp Notification", d.name)
                # Check time window (within the scheduled hour)
                if alert.schedule_time:
                    scheduled = str(alert.schedule_time)[:5]  # "08:00"
                    if current_time[:2] != scheduled[:2]:      # compare hours
                        continue
                alert.send_scheduled_message()
            except Exception:
                frappe.log_error(frappe.get_traceback(), f"Monthly WhatsApp Notification: {d.name}")


def trigger_monthly_notifications():
    trigger_notifications(method="monthly")


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
            if raw_value is None or raw_value == "":
                raw_value = field.fallback_value or ""

            fmt = field.get("field_format") or "Text"
            try:
                if fmt == "Currency (SAR)":
                    value = f"{float(raw_value):,.2f} SAR" if raw_value else "0.00 SAR"
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
           