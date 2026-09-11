// Copyright (c) 2026, MUBTKIR and contributors
// For license information, please see license.txt

frappe.ui.form.on("WhatsApp Contact Puller", {
	refresh(frm) {
		frm.dashboard.clear_headline();
		if (frm.doc.pulled_count) {
			frm.dashboard.set_headline(
				__("Pulled {0} · Selected {1}", [frm.doc.pulled_count || 0, frm.doc.selected_count || 0])
			);
		}

		// Live update of the selected counter when a checkbox is toggled.
		frm.fields_dict.pulled_numbers.grid.wrapper.on("change", ".grid-row-check, [data-fieldname='selected']", () => {
			const n = (frm.doc.pulled_numbers || []).filter((r) => r.selected).length;
			frm.set_value("selected_count", n);
		});
	},

	pull_button(frm) {
		if (frm.is_dirty()) {
			frappe.msgprint(__("Please save the document before pulling."));
			return;
		}
		frappe.call({
			method: "frappe_whatsapp.frappe_whatsapp.doctype.whatsapp_contact_puller.whatsapp_contact_puller.pull",
			args: { docname: frm.doc.name },
			freeze: true,
			freeze_message: __("Pulling numbers from WhatsApp..."),
			callback: (r) => {
				if (r.message) {
					frappe.show_alert({ message: __("Pulled {0} numbers", [r.message.pulled]), indicator: "green" });
					frm.reload_doc();
				}
			},
		});
	},

	select_all(frm) {
		// Toggle: if everything is selected, clear; otherwise select all.
		const rows = frm.doc.pulled_numbers || [];
		const all_selected = rows.length > 0 && rows.every((r) => r.selected);
		frappe.call({
			method: "frappe_whatsapp.frappe_whatsapp.doctype.whatsapp_contact_puller.whatsapp_contact_puller.toggle_select_all",
			args: { docname: frm.doc.name, value: all_selected ? 0 : 1 },
			freeze: true,
			callback: () => frm.reload_doc(),
		});
	},

	save_button(frm) {
		if (frm.is_dirty()) {
			frappe.msgprint(__("Please save the document first."));
			return;
		}
		frappe.call({
			method: "frappe_whatsapp.frappe_whatsapp.doctype.whatsapp_contact_puller.whatsapp_contact_puller.save_selected",
			args: { docname: frm.doc.name },
			freeze: true,
			freeze_message: __("Saving selected numbers..."),
			callback: (r) => {
				if (!r.message) return;
				if (r.message.queued) {
					frappe.msgprint(__("Saving {0} numbers in the background. You will be notified when done.", [r.message.count]));
				} else if (r.message.destination === "Lead") {
					frappe.msgprint(__("Created {0} leads ({1} already existed).", [r.message.created, r.message.skipped]));
				} else if (r.message.destination === "Recipient List") {
					frappe.msgprint(__("Added {0} numbers to list {1}.", [r.message.added, r.message.list]));
				}
			},
		});
	},

	export_button(frm) {
		if (frm.is_dirty()) {
			frappe.msgprint(__("Please save the document first."));
			return;
		}
		const url =
			"/api/method/frappe_whatsapp.frappe_whatsapp.doctype.whatsapp_contact_puller.whatsapp_contact_puller.export_excel" +
			"?docname=" + encodeURIComponent(frm.doc.name);
		window.open(url, "_blank");
	},
});

// Background-save completion notice.
frappe.realtime.on("whatsapp_puller_saved", (data) => {
	frappe.show_alert({ message: __("Save complete for {0}", [data.docname]), indicator: "green" });
});
