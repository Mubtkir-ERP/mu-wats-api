// Copyright (c) 2022, Shridhar Patil and contributors
// For license information, please see license.txt

frappe.ui.form.on('WhatsApp Settings', {
    refresh(frm) {
        set_channel_help(frm);
    },
    default_channel(frm) {
        set_channel_help(frm);
    }
});

function set_channel_help(frm) {
    const channel = frm.doc.default_channel || 'Meta';
    const field = frm.get_field('default_channel');
    if (!field) return;
    if (channel === 'Evolution') {
        field.set_description(__('All send paths will use Evolution by default. A message can still override the channel individually.'));
    } else {
        field.set_description(__('All send paths will use Meta Cloud API by default. A message can still override the channel individually.'));
    }
}
