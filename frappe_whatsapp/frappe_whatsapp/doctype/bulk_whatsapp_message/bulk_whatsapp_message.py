# Bulk WhatsApp Messaging for Frappe WhatsApp
# bulk_whatsapp_messaging.py

import frappe
from frappe import _
import json
import time
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
    
    def validate_message(self):
        if not self.message_content:
            frappe.throw(_("Message content is required"))
    
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
    
    def send_messages(self):
        """Send messages directly (synchronously) with progress updates"""
        recipients_list = []
        
        if self.recipient_type == 'Recipient List' and self.recipient_list:
            # Fetch recipients from the recipient list
            recipients_list = frappe.get_all(
                "WhatsApp Recipient", 
                filters={"parent": self.recipient_list},
                fields=["mobile_number", "name", "recipient_name", "recipient_data"]
            )
        else:
            # Use recipients from the current document
            for recipient in self.recipients:
                recipients_list.append({
                    "mobile_number": recipient.mobile_number,
                    "recipient_name": recipient.recipient_name,
                    "recipient_data": recipient.recipient_data or "{}"
                })
        
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
        
        frappe.msgprint(
            _("Bulk WhatsApp Message completed: {0} sent, {1} failed").format(sent, failed),
            indicator="green" if failed == 0 else "orange",
            alert=True
        )
    
    def send_single_message(self, recipient):
        """Send a single message via Evolution API. Returns True on success, False on failure."""
        
        # Add delay between messages to prevent blocking
        delay = cint(self.message_delay) or 5
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
        
        # Get Evolution Phone Settings - check user first, then sender_number
        user_evolution_settings = frappe.db.get_value(
            "Evolution Phone Settings",
            {"user": frappe.session.user},
            "name"
        )
        if user_evolution_settings:
            evolution_settings = frappe.get_doc("Evolution Phone Settings", user_evolution_settings)
        else:
            evolution_settings = frappe.get_doc("Evolution Phone Settings", self.sender_number)
        
        if not evolution_settings.base_url or not evolution_settings.instance_name:
            frappe.log_error("Evolution Phone Settings not configured", "WhatsApp Bulk Messaging")
            self.db_set("status", "Partially Failed")
            return
        
        headers = {
            "Content-Type": "application/json",
            "apikey": evolution_settings.global_api_key
        }
        
        success = False
        response_data = None
        error_message = None
        message_text = None
        
        try:
            # Get template if using template
            if self.use_template and self.template:
                template = frappe.db.get_value(
                    "WhatsApp Templates", self.template,
                    fieldname='*'
                )
                
                if not template:
                    frappe.log_error(f"Template {self.template} not found", "WhatsApp Bulk Messaging")
                    return
                
                # Build parameters for template
                parameters = []
                if recipient.get("recipient_data") and self.variable_type == 'Unique':
                    try:
                        params = list(json.loads(recipient.get("recipient_data", "{}")).values())
                        parameters = params
                    except:
                        pass
                elif self.template_variables and self.variable_type == 'Common':
                    try:
                        params = list(json.loads(self.template_variables).values())
                        parameters = params
                    except:
                        pass
                
                # Build message text from template - 'template' is the actual field name
                message_text = (
                    template.get("template") or 
                    template.get("message_content") or 
                    template.get("body") or 
                    template.get("message") or
                    ""
                )
                
                # Replace parameters in message text
                for i, param in enumerate(parameters, 1):
                    message_text = message_text.replace(f"{{{{{i}}}}}", str(param))
                
                # Handle attachments
                attachment_url = None
                filename = None
                
                if self.attach:
                    if self.attach.startswith("http"):
                        attachment_url = self.attach
                    else:
                        attachment_url = f'{frappe.utils.get_url()}{self.attach}'
                    filename = self.attach.split("/")[-1] if "/" in self.attach else self.attach
                
                # Determine content type and endpoint
                if attachment_url:
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
            else:
                # Non-template message (plain text)
                message_text = "Bulk message"  # Fallback
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
                
                # Create WhatsApp Message record for tracking
                new_doc = {
                    "doctype": "WhatsApp Message",
                    "type": "Outgoing",
                    "message": message_text,
                    "to": phone_number,
                    "message_type": "Template" if self.use_template else "Text",
                    "message_id": message_id,
                    "content_type": content_type,
                    "status": "Success",
                    "bulk_message_reference": self.name
                }
                
                if self.use_template:
                    new_doc.update({
                        "use_template": 1,
                        "template": self.template,
                        "template_parameters": recipient.get("recipient_data") if self.variable_type == 'Unique' else self.template_variables
                    })
                
                frappe.get_doc(new_doc).insert(ignore_permissions=True)
            else:
                error_message = response_data.get("response", {}).get("message", "Unknown Error")
                frappe.log_error(f"Failed to send WhatsApp message: {error_message}", "WhatsApp Bulk Messaging")
                
                # Create failed message record
                frappe.get_doc({
                    "doctype": "WhatsApp Message",
                    "type": "Outgoing",
                    "message": message_text,
                    "to": phone_number,
                    "message_type": "Template" if self.use_template else "Text",
                    "status": "Failed",
                    "bulk_message_reference": self.name
                }).insert(ignore_permissions=True)
                
        except requests.exceptions.RequestException as e:
            error_message = f"Connection error: {str(e)}"
            frappe.log_error(error_message, "WhatsApp Bulk Messaging")
        except Exception as e:
            error_message = str(e)
            frappe.log_error(error_message, "WhatsApp Bulk Messaging")
        finally:
            # Update sent count
            self.db_set("sent_count", cint(self.sent_count) + 1)
        
        return success
    
    def format_number(self, number):
        """Format phone number - remove leading + if present"""
        if number and number.startswith("+"):
            number = number[1:]
        return number

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
