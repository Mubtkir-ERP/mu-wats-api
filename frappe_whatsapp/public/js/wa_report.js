// Copyright (c) 2026, contributors. See license.txt
// Global sharing for Query, Script, Custom and Report Builder views in any app.
(function () {
"use strict";
frappe.provide("frappe_whatsapp");
if (frappe_whatsapp.report_share) return;
const WSR = "frappe_whatsapp.frappe_whatsapp.doctype.whatsapp_scheduled_report.whatsapp_scheduled_report";
let timer = null;
let observer = null;

function same_route(route) {
    return JSON.stringify(frappe.get_route()) === JSON.stringify(route);
}

function active_report(route) {
    if (route[0] === "query-report") {
        const qr = frappe.query_report;
        if (!qr || !qr.page || qr.report_name !== route[1]) return null;
        return { page: qr.page, report_name: qr.report_name, route,
            get_filters: () => qr.get_filter_values(true), source: "query" };
    }
    if (route[0] === "List" && route[2] === "Report") {
        const view = window.cur_list || (frappe.get_list_view && frappe.get_list_view());
        if (!view || !view.page || view.doctype !== route[1] ||
            (view.view_name !== "Report" && view.view !== "Report") ||
            (view.report_name || "") !== (route[3] || "") ||
            !view.get_args || !view.columns || !view.columns.length) return null;
        return { page: view.page, report_name: view.report_name || "", route,
            title: view.report_name || view.doctype, source: "builder",
            get_filters: () => ({}),
            get_builder: () => ({
                args: view.get_args(),
                columns: view.columns.filter(c => c.id).map(c => ({
                    fieldname: c.id, label: (c.docfield && c.docfield.label) || c.id,
                })),
            }),
        };
    }
    return null;
}

function attach(qr) {
    const page = qr.page;
    page.remove_inner_button(__("Send to WhatsApp"));
    const button = page.add_inner_button(__("Send to WhatsApp"), () => open_dialog(qr));
    if (observer) observer.disconnect();
    const toolbar = page.inner_toolbar && page.inner_toolbar[0];
    const element = button && button[0];
    if (toolbar && element && window.MutationObserver) {
        observer = new MutationObserver(() => {
            if (!element.isConnected && same_route(qr.route)) {
                observer.disconnect();
                // Frappe and custom report scripts may rebuild the toolbar.
                setTimeout(() => { if (same_route(qr.route)) attach(qr); }, 0);
            }
        });
        observer.observe(toolbar, {childList: true, subtree: true});
    }
}

function schedule() {
    if (timer) clearInterval(timer);
    if (observer) observer.disconnect();
    const route = frappe.get_route().slice();
    if (route[0] !== "query-report" && !(route[0] === "List" && route[2] === "Report")) return;
    let tries = 0;
    const attempt = () => {
        if (!same_route(route)) { clearInterval(timer); return; }
        const qr = active_report(route);
        if (qr) { clearInterval(timer); attach(qr); }
        else if (++tries >= 100) clearInterval(timer);
    };
    timer = setInterval(attempt, 300);
    attempt();
}

function collect(qr, d) {
    if (!same_route(qr.route)) {
        frappe.msgprint(__("The report changed. Close this dialog and open it from the current report."));
        return null;
    }
    const v = d.get_values();
    if (!v) return null;
    if (!v.mobile_number && !(v.party_type && v.party)) {
        frappe.msgprint(__("Enter a recipient mobile number or select a party."));
        return null;
    }
    const filters = qr.get_filters(); // Let Frappe validate mandatory report filters.
    if (filters === undefined || filters === null) return null;
    return {
        report: qr.report_name, report_title: qr.title || qr.report_name,
        filters: JSON.stringify(filters),
        builder_spec: qr.source === "builder" ? JSON.stringify(qr.get_builder()) : "{}",
        report_format: v.report_format || "PDF", party_type: v.party_type || "",
        party: v.party || "", mobile_number: v.mobile_number || "",
        whatsapp_instance: v.whatsapp_instance || "", use_template: v.use_template ? 1 : 0,
        template: v.template || "", caption: v.caption || "",
    };
}

// Custom app pages backed by a registered Query/Script Report can opt in.
frappe_whatsapp.report_share = {
    attach(options) {
        if (!options.page || !options.report_name || typeof options.get_filters !== "function") {
            throw new Error("page, report_name and get_filters are required");
        }
        attach(Object.assign({route: frappe.get_route().slice(), source: "query"}, options));
    },
};
frappe.router.on("change", schedule);
schedule();

function open_dialog(qr) {
    if (!same_route(qr.route)) return;
    const operation_key = window.crypto && window.crypto.randomUUID
        ? window.crypto.randomUUID() : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
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
			const args = collect(qr, d);
			if (!args) return;
			args.operation_key = operation_key;
			frappe.call({
				method: `${WSR}.send_report_adhoc`,
				args,
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
			if (!qr.report_name) {
				frappe.msgprint(__("Save this Report Builder report before scheduling it."));
				return;
			}
			const args = collect(qr, d);
			if (!args) return;
			frappe.call({
				method: `${WSR}.create_scheduled_report`,
				args,
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

})();
