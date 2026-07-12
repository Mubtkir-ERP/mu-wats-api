// Copyright (c) 2026, Shridhar Patil and contributors
// For license information, please see license.txt

// Map connection status -> colour used for the circular indicator.
const WA_STATUS_COLORS = {
	Connected: "green",
	Disconnected: "red",
	Connecting: "orange",
};

frappe.ui.form.on("Whatsapp Instance", {
	refresh(frm) {
		render_status_indicator(frm);
		add_action_buttons(frm);
		maybe_autofetch_phone(frm);
	},

	connection_status(frm) {
		render_status_indicator(frm);
	},

	linked_user(frm) {
		// Early warning: block picking a user who already owns an instance,
		// before the document is even saved.
		if (!frm.doc.linked_user) {
			return;
		}

		frappe.call({
			method: "frappe_whatsapp.api.check_user_instance",
			args: { user: frm.doc.linked_user },
			callback: function (r) {
				const existing = r.message;
				// Ignore a match against the current document itself.
				if (existing && existing !== frm.doc.name) {
					frappe.msgprint({
						title: __("User Already Linked"),
						indicator: "red",
						message: __(
							"User {0} is already linked to instance {1}. Only one instance is allowed per user.",
							[frm.doc.linked_user.bold(), existing.bold()]
						),
					});
					frm.set_value("linked_user", null);
				}
			},
		});
	},
});

// If the instance is Connected but the phone number is still empty, fetch it
// once from the Mubtkir API and fill it in. Guarded so it runs at most once
// per form load (avoids a refresh loop when ownerJid is unavailable).
function maybe_autofetch_phone(frm) {
	if (frm.is_new()) {
		return;
	}
	if (frm.doc.connection_status !== "Connected" || frm.doc.phone_number) {
		return;
	}
	if (frm.__phone_autofetch_done) {
		return;
	}
	frm.__phone_autofetch_done = true;

	frappe.call({
		method: "frappe_whatsapp.api.get_instance_status",
		args: { instance_name: frm.doc.name },
		callback: function (r) {
			const msg = r.message || {};
			if (msg.phone_number) {
				// Set directly (not via set_value) so the form is not marked
				// dirty — the value is already persisted server-side.
				frm.doc.phone_number = msg.phone_number;
				frm.refresh_field("phone_number");
			}
			render_status_indicator(frm, msg.status);
		},
	});
}

// Render the colored circular status indicator. Accepts an optional status
// override so it can be refreshed live during polling without a full reload.
function render_status_indicator(frm, status) {
	status = status || frm.doc.connection_status || "Disconnected";
	const color = WA_STATUS_COLORS[status] || "red";

	// Colored dot in the form header.
	if (!frm.is_new()) {
		frm.page.set_indicator(__(status), color);
	}

	// Circular colored dot rendered inside the HTML field.
	const dot = `
		<div style="display:flex; align-items:center; gap:10px; padding:6px 0;">
			<span style="
				display:inline-block;
				width:14px; height:14px;
				border-radius:50%;
				background:${color};
				box-shadow:0 0 0 3px ${color}33;">
			</span>
			<span style="font-weight:600;">${__(status)}</span>
		</div>`;
	const field = frm.get_field("status_indicator");
	if (field) {
		field.$wrapper.html(dot);
	}
}

function add_action_buttons(frm) {
	if (frm.is_new()) {
		return;
	}

	// Whether the instance is already registered on the Mubtkir API decides
	// whether we show "Create Instance" or "Show QR Code". Resolve it on the
	// server, then (re)build the buttons in the required order.
	frappe.call({
		method: "frappe_whatsapp.api.is_registered",
		args: { instance_name: frm.doc.name },
		callback: function (r) {
			build_action_buttons(frm, !!(r && r.message));
		},
	});
}

function build_action_buttons(frm, registered) {
	// Rebuild from scratch so order and visibility are always correct.
	frm.clear_custom_buttons();

	const status = frm.doc.connection_status || "";

	// 1. Create Instance (primary, blue) — only before it exists in Mubtkir API,
	//    and only while Disconnected/empty.
	if (!registered && (status === "" || status === "Disconnected")) {
		const btn = frm.add_custom_button(__("Create Instance"), () => create_instance(frm));
		btn.removeClass("btn-default").addClass("btn-primary");
	}

	// 2. Show QR Code (default) — after the instance has been created and while
	//    it is not yet connected.
	if (registered && status !== "Connected") {
		frm.add_custom_button(__("Show QR Code"), () => show_qr_dialog(frm));
	}

	// 3. Check Status (default) — always available.
	frm.add_custom_button(__("Check Status"), () => check_status(frm));

	// 4. Sync with Mubtkir API (default) — reconcile this record against the
	//    Mubtkir API server. If the instance was deleted there, the registration
	//    is cleared so the "Create Instance" button reappears for re-creation.
	frm.add_custom_button(__("Sync with Mubtkir API"), () => sync_with_evolution(frm));

	// 5. Disconnect (danger, red) — only when currently connected.
	if (status === "Connected") {
		const btn = frm.add_custom_button(__("Disconnect"), () => confirm_disconnect(frm));
		btn.removeClass("btn-default").addClass("btn-danger");
	}
}

function sync_with_evolution(frm) {
	frappe.call({
		method: "frappe_whatsapp.api.sync_instance_with_evolution",
		args: { instance_name: frm.doc.name },
		freeze: true,
		freeze_message: __("Syncing with Mubtkir API..."),
		callback: function (r) {
			if (r.exc) {
				return;
			}
			const msg = r.message || {};
			if (msg.exists === false) {
				// Removed on the server — registration was cleared locally.
				frappe.msgprint({
					title: __("Instance Not Found on Mubtkir API"),
					indicator: "orange",
					message: __(
						"This instance no longer exists on the Mubtkir API server. The record has been kept and reset — use \"Create Instance\" to re-create it under the same name."
					),
				});
			} else {
				frappe.show_alert({
					message: __("In sync. Connection status: {0}", [msg.status || "Disconnected"]),
					indicator: WA_STATUS_COLORS[msg.status] || "blue",
				});
			}
			// Reload so buttons rebuild (Create Instance reappears when cleared).
			frm.reload_doc();
		},
	});
}

function create_instance(frm) {
	frappe.confirm(
		__(
			"Create this instance in Mubtkir API? This will register the instance name and prepare it for QR scanning."
		),
		function () {
			frappe.call({
				method: "frappe_whatsapp.api.create_whatsapp_instance",
				args: { instance_name: frm.doc.name },
				freeze: true,
				freeze_message: __("Creating instance in Mubtkir API..."),
				callback: function (r) {
					// On error, Frappe already shows the exact server message;
					// do not repeat it here.
					if (r.exc) {
						return;
					}
					frappe.msgprint({
						title: __("Success"),
						indicator: "green",
						message: __(
							"Instance created successfully. You can now scan the QR code."
						),
					});
					// Reload so the buttons rebuild and "Show QR Code" appears.
					frm.reload_doc();
				},
			});
		}
	);
}

function check_status(frm) {
	frappe.call({
		method: "frappe_whatsapp.api.get_instance_status",
		args: { instance_name: frm.doc.name },
		freeze: true,
		freeze_message: __("Checking connection status..."),
		callback: function (r) {
			const msg = r.message || {};
			const status = msg.status || "Disconnected";
			frappe.show_alert({
				message: __("Connection status: {0}", [status]),
				indicator: WA_STATUS_COLORS[status] || "red",
			});
			frm.reload_doc();
		},
	});
}

function confirm_disconnect(frm) {
	frappe.confirm(
		__("Are you sure you want to disconnect this WhatsApp instance?"),
		function () {
			frappe.call({
				method: "frappe_whatsapp.api.disconnect_instance",
				args: { instance_name: frm.doc.name },
				freeze: true,
				freeze_message: __("Disconnecting..."),
				callback: function () {
					frappe.show_alert({
						message: __("The instance has been disconnected."),
						indicator: "orange",
					});
					frm.reload_doc();
				},
			});
		}
	);
}

function show_qr_dialog(frm) {
	const dialog = new frappe.ui.Dialog({
		title: __("Scan the QR Code with WhatsApp"),
		size: "small",
		fields: [{ fieldtype: "HTML", fieldname: "qr_area" }],
	});

	let poll_interval = null;

	const stop_polling = () => {
		if (poll_interval) {
			clearInterval(poll_interval);
			poll_interval = null;
		}
	};

	// Clean up the timer whenever the dialog is closed.
	dialog.onhide = stop_polling;

	const set_message = (html) => {
		dialog.fields_dict.qr_area.$wrapper.html(
			`<div style="text-align:center; padding:16px;">${html}</div>`
		);
	};

	const render_qr = (data) => {
		if (data.status === "Connected") {
			on_connected();
			return;
		}

		if (data.base64) {
			let img = data.base64;
			if (!img.startsWith("data:image")) {
				img = "data:image/png;base64," + img;
			}
			set_message(`
				<p style="color:#555; margin-bottom:12px;">
					${__("Open WhatsApp on your phone, go to Settings → Linked Devices → Link a Device.")}
				</p>
				<img src="${img}"
					style="width:260px; height:260px; border:3px solid #25D366; border-radius:12px; padding:5px;" />
				<p style="margin-top:12px; color:#888; font-size:12px;">
					${__("Waiting for the connection... this dialog will refresh automatically.")}
				</p>`);
		} else {
			set_message(
				`<p style="color:#d9534f;">${__(
					"Could not fetch a QR code. Please verify the Mubtkir API Server settings and try again."
				)}</p>`
			);
		}
	};

	const on_connected = (phone_number) => {
		stop_polling();
		// Keep the header indicator in sync immediately.
		render_status_indicator(frm, "Connected");
		// Reflect the auto-fetched phone number in the form right away.
		if (phone_number) {
			frm.set_value("phone_number", phone_number);
		}
		frappe.show_alert(
			{ message: __("WhatsApp connected successfully!"), indicator: "green" },
			5
		);
		set_message(`
			<i class="fa fa-check-circle" style="font-size:60px; color:green;"></i>
			<h3 style="color:green; margin-top:15px;">${__("Connected successfully")}</h3>
			<p>${__("This window will close automatically.")}</p>`);
		// Auto-close 3 seconds after a successful connection.
		setTimeout(() => {
			dialog.hide();
			frm.reload_doc();
		}, 3000);
	};

	const fetch_qr = () => {
		frappe.call({
			method: "frappe_whatsapp.api.get_qr_code",
			args: { instance_name: frm.doc.name },
			callback: function (r) {
				if (r.message) {
					render_qr(r.message);
				}
			},
		});
	};

	const poll_status = () => {
		frappe.call({
			method: "frappe_whatsapp.api.get_instance_status",
			args: { instance_name: frm.doc.name },
			callback: function (r) {
				const msg = r.message || {};
				const status = msg.status || "Disconnected";
				// Update the status dot after every poll.
				render_status_indicator(frm, status);
				if (status === "Connected") {
					on_connected(msg.phone_number);
				}
			},
		});
	};

	set_message(`<p>${__("Loading QR code...")}</p>`);
	dialog.show();
	fetch_qr();

	// Poll the connection state every 2 seconds.
	poll_interval = setInterval(poll_status, 2000);
}
