// Copyright (c) 2026, Shridhar Patil and contributors
// For license information, please see license.txt
//
// Adds a "Send to WhatsApp" button to every query/script report view. It sends
// the report exactly as currently filtered/columned, or saves the current
// filters as a scheduled report — so report filters are chosen with the native
// report UI, never re-implemented.

frappe.provide("frappe_whatsapp");

const WSR = "frappe_whatsapp.frappe_whatsapp.doctype.whatsapp_scheduled_report.whatsapp_scheduled_report";

frappe.router.on("change", () => {
	const route = frappe.get_route();
	if (!route || route[0] !== "query-report") {
		return;
	}
	// The report loads asynchronously; retry briefly until it's ready.
	let tries = 0;
	const timer = setInterval(() => {
		tries += 1;
		const qr = frappe.query_report;
		if (qr && qr.page && qr.report_name) {
			clearInterval(timer);
			add_button(qr);
		} else if (tries > 30) {
			clearInterval(timer);
		}
	}, 300);
});

function add_button(qr) {
	// Keep exactly one button, even as the user moves between reports.
	qr.page.remove_inner_button(__("Send to WhatsApp"));
	qr.page.add_inner_button(__("Send to WhatsApp"), () => open_dialog(qr));
}

function current_filters(qr) {
	try {
		return JSON.stringify(qr.get_filter_values ? qr.get_filter_values(false) : {});
	} catch (e) {
		return "{}";
	}
}

function collect(qr, d) {
	const v = d.get_values(true) || {};
	return {
		report: qr.report_name,
		filters: current_filters(qr),
		report_format: v.report_format || "PDF",
		party_type: v.party_type || "",
		party: v.party || "",
		mobile_number: v.mobile_number || "",
		whatsapp_instance: v.whatsapp_instance || "",
		use_template: v.use_template ? 1 : 0,
		template: v.template || "",
		caption: v.caption || "",
	};
}

function open_dialog(qr) {
	const d = new frappe.ui.Dialog({
		title: __("Send Report to WhatsApp"),
		fields: [
			{
				fieldtype: "Select",
				fieldname: "report_format",
				label: __("Format"),
				options: "PDF\nExcel\nCSV",
				default: "PDF",
				reqd: 1,
			},
			{ fieldtype: "Section Break", label: __("Recipient") },
			{
				fieldtype: "Select",
				fieldname: "party_type",
				label: __("Party Type"),
				options: "\nCustomer\nSupplier\nEmployee\nShareholder",
			},
			{ fieldtype: "Dynamic Link", fieldname: "party", label: __("Party"), options: "party_type" },
			{ fieldtype: "Column Break" },
			{ fieldtype: "Data", fieldname: "mobile_number", label: __("Recipient Mobile (override)") },
			{
				fieldtype: "Link",
				fieldname: "whatsapp_instance",
				label: __("Send From Instance"),
				options: "Whatsapp Instance",
			},
			{ fieldtype: "Section Break", label: __("Message") },
			{ fieldtype: "Check", fieldname: "use_template", label: __("Use WhatsApp Template for Message") },
			{
				fieldtype: "Link",
				fieldname: "template",
				label: __("Message Template"),
				options: "WhatsApp Templates",
				depends_on: "use_template",
			},
			{
				fieldtype: "Small Text",
				fieldname: "caption",
				label: __("Message Caption"),
				depends_on: "eval:!doc.use_template",
			},
		],
		primary_action_label: __("Send Now"),
		primary_action() {
			frappe.call({
				method: `${WSR}.send_report_adhoc`,
				args: collect(qr, d),
				freeze: true,
				freeze_message: __("Rendering and sending report..."),
				callback: function (r) {
					if (r.exc) {
						return;
					}
					frappe.show_alert({ message: __("Report sent."), indicator: "green" });
					d.hide();
				},
			});
		},
		secondary_action_label: __("Save as Scheduled"),
		secondary_action() {
			frappe.call({
				method: `${WSR}.create_scheduled_report`,
				args: collect(qr, d),
				freeze: true,
				callback: function (r) {
					if (r.exc || !r.message) {
						return;
					}
					d.hide();
					frappe.show_alert({
						message: __("Saved. Review the schedule and enable it."),
						indicator: "blue",
					});
					frappe.set_route("Form", "WhatsApp Scheduled Report", r.message.name);
				},
			});
		},
	});
	d.show();
}
