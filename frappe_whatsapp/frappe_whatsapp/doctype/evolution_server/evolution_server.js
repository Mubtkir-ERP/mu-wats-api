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
			const ok = res.ok;
			const indicator = ok ? "green" : "red";
			const title = ok
				? __("Connection Successful")
				: __("Connection Failed");

			// Show the raw response exactly as returned by the server.
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
				title: title,
				size: "large",
				fields: [{ fieldtype: "HTML", fieldname: "result" }],
			});
			dialog.get_field("result").$wrapper.html(html);
			dialog.show();

			frappe.show_alert({
				message: ok
					? __("Reached the Evolution API (HTTP {0}).", [res.status_code])
					: __("Could not reach the Evolution API. See the dialog for details."),
				indicator: indicator,
			});
		},
	});
}
