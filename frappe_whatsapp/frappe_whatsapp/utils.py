import frappe


def run_server_script_for_doc_event(doc, method):
    """Run WhatsApp notifications for document events."""

    # Map method names to doctype_event values
    method_map = {
        "before_insert": "Before Insert",
        "after_insert": "After Insert",
        "before_validate": "Before Validate",
        "validate": "Before Save",
        "on_update": "After Save",
        "before_submit": "Before Submit",
        "on_submit": "After Submit",
        "before_cancel": "Before Cancel",
        "on_cancel": "After Cancel",
        "on_trash": "Before Delete",
        "after_delete": "After Delete",
        "before_update_after_submit": "Before Save (Submitted Document)",
        "on_update_after_submit": "After Save (Submitted Document)"
    }

    doctype_event = method_map.get(method)
    if not doctype_event:
        return

    # Find matching notifications
    notifications = frappe.get_all(
        "WhatsApp Notification",
        filters={
            "notification_type": "DocType Event",
            "reference_doctype": doc.doctype,
            "doctype_event": doctype_event,
            "disabled": 0
        }
    )

    for n in notifications:
        try:
            notification = frappe.get_doc("WhatsApp Notification", n.name)
            notification.send_template_message(doc)
        except Exception:
            frappe.log_error(
                frappe.get_traceback(),
                f"WhatsApp Notification Failed: {n.name} for {doc.doctype} {doc.name}"
            )


def trigger_whatsapp_notifications_all():
    pass


def trigger_whatsapp_notifications_hourly():
    pass


def trigger_whatsapp_notifications_hourly_long():
    pass


def trigger_whatsapp_notifications_daily():
    from frappe_whatsapp.frappe_whatsapp.doctype.whatsapp_notification.whatsapp_notification import trigger_notifications
    trigger_notifications(method="daily")


def trigger_whatsapp_notifications_daily_long():
    pass


def trigger_whatsapp_notifications_weekly():
    pass


def trigger_whatsapp_notifications_weekly_long():
    pass


def trigger_whatsapp_notifications_monthly():
    pass


def trigger_whatsapp_notifications_monthly_long():
    pass
