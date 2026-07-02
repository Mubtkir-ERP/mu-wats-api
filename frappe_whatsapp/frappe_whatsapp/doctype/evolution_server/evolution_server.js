// Copyright (c) 2026, Shridhar Patil and contributors
// For license information, please see license.txt

frappe.ui.form.on("Evolution Server", {
	refresh(frm) {
		if (frm.is_new()) {
			return;
		}

		frm.add_custom_button(__("Test Connection"), () => test_connection(frm));
	},
});

function test_connection(frm) {
	frappe.call({
		doc: frm.doc,
		method: "test_connection",
		freeze: true,
		freeze_message: __("Contacting Evolution API..."),
		callback: function (r) {
			const res = r.message || {};

			// Refresh so the read-only connection_status field reflects the
			// value the server just saved (Online / Offline).
			frm.reload_doc();

			if (res.ok) {
				// Clean success: no URL, status or raw response.
				const dialog = new frappe.ui.Dialog({
					title: __("Connection Successful"),
					fields: [{ fieldtype: "HTML", fieldname: "result" }],
				});
				dialog.get_field("result").$wrapper.html(`
					<div style="text-align:center; padding:16px;">
						<i class="fa fa-check-circle" style="font-size:56px; color:green;"></i>
						<h3 style="color:green; margin-top:14px;">
							${__("Evolution API server is reachable and responding correctly.")}
						</h3>
					</div>`);
				dialog.show();
				return;
			}

			// Failure: show full details for debugging.
			const body_text = typeof res.body === "string"
				? res.body
				: JSON.stringify(res.body, null, 2);

			const html = `
				<div style="padding:8px;">
					<p><b>${__("URL")}:</b> ${frappe.utils.escape_html(res.url || "")}</p>
					<p><b>${__("HTTP Status")}:</b> ${res.status_code === null || res.status_code === undefined ? "—" : res.status_code}</p>
					<p><b>${__("Raw Response")}:</b></p>
					<pre style="max-height:320px; overflow:auto; background:#f5f5f5; padding:10px; border-radius:6px; white-space:pre-wrap; word-break:break-word;">${frappe.utils.escape_html(body_text || "")}</pre>
				</div>`;

			const dialog = new frappe.ui.Dialog({
				title: __("Connection Failed"),
				size: "large",
				fields: [{ fieldtype: "HTML", fieldname: "result" }],
			});
			dialog.get_field("result").$wrapper.html(html);
			dialog.show();

			frappe.show_alert({
				message: __("Could not reach the Evolution API. See the dialog for details."),
				indicator: "red",
			});
		},
	});
}
