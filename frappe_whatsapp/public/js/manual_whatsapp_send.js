/* One-click resend through the existing automatic WhatsApp notification template. */
;(function () {
    ["Sales Invoice", "Quotation"].forEach(function (doctype) {
        frappe.ui.form.on(doctype, {
            refresh: function (frm) {
                if (frm.is_new()) return;
                frm.add_custom_button(__("Send WhatsApp"), function () {
                    if (frm.is_dirty()) {
                        frappe.msgprint(__("Please save the document before sending WhatsApp."));
                        return;
                    }
                    frappe.call({
                        method: "frappe_whatsapp.manual_whatsapp.send_document_message",
                        args: {doctype: frm.doctype, name: frm.doc.name},
                        freeze: true,
                        freeze_message: __("Sending via the configured WhatsApp template..."),
                        callback: function (r) {
                            if (r.message && r.message.status === "Sent") {
                                frappe.show_alert({message: __("WhatsApp message sent"), indicator: "green"});
                            } else {
                                frappe.msgprint(__("WhatsApp delivery was not confirmed. Check WhatsApp Message logs."));
                            }
                        }
                    });
                });
            }
        });
    });
})();
