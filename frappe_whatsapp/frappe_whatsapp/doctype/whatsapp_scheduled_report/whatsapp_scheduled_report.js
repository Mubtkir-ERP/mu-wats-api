// Copyright (c) 2026, Shridhar Patil and contributors
// For license information, please see license.txt

frappe.ui.form.on("WhatsApp Scheduled Report", {
	refresh(frm) {
		if (frm.is_new()) {
			return;
		}
		const btn = frm.add_custom_button(__("Send Now"), () => {
			frappe.confirm(__("Render and send this report now?"), () => {
				frappe.call({
					doc: frm.doc,
					method: "send_now",
					freeze: true,
					freeze_message: __("Rendering and sending report..."),
					callback: function (r) {
						if (r.exc) {
							return;
						}
						frappe.show_alert({ message: __("Report sent."), indicator: "green" });
						frm.reload_doc();
					},
				});
			});
		});
		btn.removeClass("btn-default").addClass("btn-primary");
	},
});
