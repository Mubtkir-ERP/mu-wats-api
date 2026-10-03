// Copyright (c) 2025, Shridhar Patil and contributors
// For license information, please see license.txt

frappe.ui.form.on("Evolution Phone Settings", {
    refresh(frm) {
        // This DocType is kept only for backward compatibility. Connection,
        // QR and logout operations are managed by the newer Whatsapp Instance
        // DocType so we do not expose buttons that call removed backend methods.
        frm.set_intro(
            __("Legacy settings. Use <b>Whatsapp Instance</b> for connection, QR code, status and logout. Existing API keys are migrated securely during update."),
            "orange"
        );

        frm.add_custom_button(__("Open Whatsapp Instances"), () => {
            frappe.set_route("List", "Whatsapp Instance");
        }, __("WhatsApp"));
    }
});
