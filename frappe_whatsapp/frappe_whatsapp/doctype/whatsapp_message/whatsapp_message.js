// Copyright (c) 2022, Shridhar Patil and contributors
// For license information, please see license.txt

frappe.ui.form.on('WhatsApp Message', {
    onload(frm) {
        // Show the configured default on new manually-created messages without
        // hardcoding Meta/Evolution in the DocType field definition.
        if (frm.is_new() && frm.doc.type !== 'Incoming' && !frm.doc.channel) {
            frappe.db.get_single_value('WhatsApp Settings', 'default_channel').then(value => {
                if (value && !frm.doc.channel) {
                    frm.set_value('channel', value);
                }
            });
        }

        frappe.db.get_single_value('WhatsApp Settings', 'allow_auto_read_receipt').then(value => {
            if (
                value &&
                frm.doc.type === 'Incoming' &&
                frm.doc.channel !== 'Evolution' &&
                !frm.doc.read_receipt_sent &&
                frm.doc.message_id
            ) {
                send_read_receipt(frm);
            }
        });
    },

    refresh(frm) {
        if (frm.doc.type === 'Incoming') {
            frm.add_custom_button(__("Reply"), function () {
                const values = {
                    to: frm.doc.from,
                    type: 'Outgoing',
                    content_type: 'text',
                    channel: frm.doc.channel || undefined,
                    send_from_instance: frm.doc.send_from_instance || undefined,
                    is_reply: 1,
                    reply_to_message_id: frm.doc.message_id || undefined,
                };
                frappe.new_doc('WhatsApp Message', values);
            });
        }
        add_mark_as_read(frm);
    }
});

function add_mark_as_read(frm) {
    if (
        frm.doc.type === 'Outgoing' ||
        frm.doc.channel === 'Evolution' ||
        frm.doc.read_receipt_sent ||
        !frm.doc.message_id
    ) return;

    frappe.db.get_single_value('WhatsApp Settings', 'allow_auto_read_receipt').then(value => {
        if (value) return;
        frm.add_custom_button(__('Mark as read'), function () {
            send_read_receipt(frm);
        });
    });
}

function send_read_receipt(frm) {
    frappe.call({
        doc: frm.doc,
        method: 'send_read_receipt',
        callback(r) {
            if (r && r.message) {
                frm.doc.read_receipt_sent = 1;
                frm.refresh_field('read_receipt_sent');
                frappe.show_alert({message: __('Marked as read'), indicator: 'green'});
            }
        }
    });
}
