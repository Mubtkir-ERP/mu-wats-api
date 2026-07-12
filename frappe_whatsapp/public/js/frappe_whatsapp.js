// Copyright (c) 2026, Shridhar Patil and contributors
// For license information, please see license.txt

frappe.provide("frappe_whatsapp");

// ═══════════════════════════════════════════════════════════════
// 1) WhatsApp Connection Status Indicator in the Navbar
//    Runs on a retry loop (NOT gated on `app_ready`, which is not
//    fired reliably across Frappe versions).
// ═══════════════════════════════════════════════════════════════
(function () {
	var previous_status = null;

	function get_navbar_list() {
		// The right-hand navbar item list, across Frappe versions.
		return $(
			"header.navbar ul.navbar-nav, " +
			".navbar .navbar-collapse ul.navbar-nav, " +
			".navbar-nav.ml-auto, .navbar-nav.ms-auto"
		).last();
	}

	function inject_indicator() {
		if (document.getElementById("wa-status-indicator")) {
			return true; // already injected
		}

		var $list = get_navbar_list();
		if (!$list.length) {
			return false; // navbar not ready yet
		}

		var $item = $(
			'<li class="nav-item" id="wa-status-indicator" title="WhatsApp Status">' +
			'<a class="nav-link" href="#" style="display:flex;align-items:center;height:100%;padding:0 12px;">' +
			'<span id="wa-status-dot" style="width:13px;height:13px;border-radius:50%;' +
			"background:#ccc;display:inline-block;box-shadow:0 0 0 3px rgba(0,0,0,0.08);" +
			'transition:background 0.3s, box-shadow 0.3s;"></span>' +
			"</a></li>"
		);

		// Prefer placing it just before the notifications bell for a stable,
		// visible position; otherwise fall back to prepending to the list.
		var $bell = $("header.navbar li.dropdown-notifications, .navbar li.dropdown-notifications").first();
		if ($bell.length) {
			$item.insertBefore($bell);
		} else {
			$list.prepend($item);
		}

		$item.find("a").on("click", function (e) {
			e.preventDefault();
			var instance_name = $item.data("instance");
			if (instance_name) {
				frappe.set_route("Form", "Whatsapp Instance", instance_name);
			} else {
				frappe.set_route("List", "Whatsapp Instance");
			}
		});

		update_status();
		return true;
	}

	function set_dot(color, tooltip) {
		var $dot = $("#wa-status-dot");
		$dot.css({ background: color, "box-shadow": "0 0 0 3px " + color + "33" });
		$("#wa-status-indicator").attr("title", tooltip);
	}

	function update_status() {
		frappe.call({
			method: "frappe_whatsapp.api.get_user_instance_status",
			async: true,
			callback: function (r) {
				if (!r.message) {
					set_dot("#9aa0a6", __("No WhatsApp instance linked"));
					return;
				}
				var status = r.message.status;
				var instance = r.message.instance_name;
				$("#wa-status-indicator").data("instance", instance);

				if (status === "Connected") {
					set_dot("#22c55e", __("WhatsApp Connected") + " — " + instance);
				} else if (status === "Connecting") {
					set_dot("#f59e0b", __("WhatsApp Connecting") + " — " + instance);
				} else {
					set_dot("#ef4444", __("WhatsApp Disconnected") + " — " + instance);
				}

				if (previous_status === "Connected" && status !== "Connected") {
					frappe.show_alert(
						{
							message: __("⚠ WhatsApp disconnected! Click the status dot to reconnect."),
							indicator: "red",
						},
						15
					);
				}
				previous_status = status;
			},
			error: function () {
				set_dot("#9aa0a6", __("WhatsApp status unavailable"));
			},
		});
	}

	// Retry injection until the navbar exists (~15s), then poll every 30s.
	var tries = 0;
	var injectTimer = setInterval(function () {
		tries += 1;
		if (inject_indicator() || tries > 40) {
			clearInterval(injectTimer);
		}
	}, 400);
	inject_indicator(); // also try immediately

	setInterval(function () {
		if (document.getElementById("wa-status-dot")) {
			update_status();
		}
	}, 30000);
})();

// ═══════════════════════════════════════════════════════════════
// 2) Colour the WhatsApp workspace number cards
//    Applies a coloured top border + coloured figure to each card,
//    matched by its (English) widget name or its visible title.
// ═══════════════════════════════════════════════════════════════
(function () {
	var COLORS = {
		"Sent Messages": "#2490EF",
		"Delivered Messages": "#1ABC9C",
		"Read Messages": "#29CD42",
		"Pending Messages": "#FFB300",
		"Failed Messages": "#E24C4C",
		"Received Messages": "#9B59B6",
		// Arabic fallbacks (in case the title is rendered translated)
		"الرسائل المرسلة": "#2490EF",
		"الرسائل المُوصّلة": "#1ABC9C",
		"الرسائل المقروءة": "#29CD42",
		"الرسائل المعلّقة": "#FFB300",
		"الرسائل الفاشلة": "#E24C4C",
		"الرسائل الواردة": "#9B59B6",
	};

	function colorize() {
		$(".widget.number-widget-box, .widget[data-widget-name], .number-card, .dashboard-widget-box").each(
			function () {
				var $w = $(this);
				var name = ($w.attr("data-widget-name") || "").trim();
				if (!name) {
					name = ($w.find(".widget-title, .widget-head .ellipsis, .card-title").first().text() || "").trim();
				}
				var color = COLORS[name];
				if (!color) {
					return;
				}
				$w.css({
					"border-top": "3px solid " + color,
					"border-top-left-radius": "8px",
					"border-top-right-radius": "8px",
				});
				$w.find(".number, .widget-body .number, .number-card-value, .stat-value").css("color", color);
			}
		);
	}

	// Widgets render asynchronously; retry a few times after each route change.
	function schedule_colorize() {
		var route = (frappe.get_route() || []).join("/").toLowerCase();
		if (route.indexOf("whatsapp") === -1) {
			return;
		}
		[200, 600, 1200, 2000].forEach(function (delay) {
			setTimeout(colorize, delay);
		});
	}

	if (frappe.router && frappe.router.on) {
		frappe.router.on("change", schedule_colorize);
	}
	schedule_colorize();
})();

// ═══════════════════════════════════════════════════════════════
// 3) "Send To WhatsApp" menu item on all forms
// ═══════════════════════════════════════════════════════════════
frappe.router.on("change", () => {
	var route = frappe.get_route();
	if (route && route[0] == "Form") {
		frappe.ui.form.on(route[1], {
			refresh: function (frm) {
				frm.page.add_menu_item(__("Send To Whatsapp"), function () {
					var user_name = frappe.user.name;
					var user_full_name = frappe.session.user_fullname;
					var reference_doctype = frm.doctype;
					var reference_name = frm.docname;
					var dialog = new frappe.ui.Dialog({
						'fields': [
							{ 'fieldname': 'ht', 'fieldtype': 'HTML' },
							{ 'label': 'Select Template', 'fieldname': 'template', 'reqd': 1, 'fieldtype': 'Link', 'options': 'WhatsApp Templates' },
							{ 'label': 'Send to', 'fieldname': 'contact', 'reqd': 1, 'fieldtype': 'Link', 'options': 'Contact', change() {
						let contact_name = dialog.get_value('contact');
						if (contact_name) {
							frappe.call({
								method: 'frappe.client.get_value',
								args: {
									doctype: 'Contact',
									filters: { name: contact_name },
									fieldname: ['mobile_no']
								},
								callback: function (r) {
									if (r.message) {
										dialog.set_value('mobile_no', r.message.mobile_no);
									} else {
										dialog.set_value('mobile_no', '');
										frappe.msgprint(__('Mobile number not found for the selected contact.'));
									}
								}
							});
						} else {
							dialog.set_value('mobile_no', '');
						}
					}},
							{ 'label': 'Mobile no', 'fieldname': 'mobile_no', 'fieldtype': 'Data' },

						],
						'primary_action_label': 'Send',
						'title': 'Send WhatsApp Message',
						primary_action: function () {
							var values = dialog.get_values();
							if (values) {
								var space = "\n" + "\n";

								frappe.call({
									method: "frappe_whatsapp.frappe_whatsapp.doctype.whatsapp_message.whatsapp_message.send_template",
									args: {
										to: values.mobile_no,
										template: values.template,
										reference_doctype: frm.doc.doctype,
										reference_name: frm.doc.name
									},
									freeze: true,
									callback: (r) => {
										frappe.msgprint(__("Successfully Sent to: " + values.mobile_no));
										dialog.hide();
									}
								});

								var comment_message = 'To : ' + values.mobile_no + space + "Whatsapp Template:" + values.template;
								frappe.call({
									method: "frappe.desk.form.utils.add_comment",
									args: {
										reference_doctype: reference_doctype,
										reference_name: reference_name,
										content: comment_message,
										comment_by: frappe.session.user_fullname,
										comment_email: frappe.session.user
									},
								});
							}

						},
						no_submit_on_enter: true,
					});
					let template = dialog.fields_dict.template;
					if (template) {
						template.get_query = function() {
							return {
								filters: { "for_doctype": frm.doc.doctype },
								doctype: "WhatsApp Templates"
							};
						};
						template.refresh();
					}
					dialog.show();

					// Auto-fill Primary Contact and mobile_no
					var party = frm.doc.customer || frm.doc.supplier || frm.doc.lead || frm.doc.party;
					var party_type = frm.doc.customer ? 'Customer' :
									 frm.doc.supplier ? 'Supplier' :
									 frm.doc.lead ? 'Lead' : null;

					if (party && party_type) {
						frappe.call({
							method: 'frappe.client.get_list',
							args: {
								doctype: 'Dynamic Link',
								filters: {
									link_doctype: party_type,
									link_name: party,
									parenttype: 'Contact'
								},
								fields: ['parent'],
								order_by: 'idx asc',
								limit_page_length: 1
							},
							callback: function(r) {
								if (r.message && r.message.length > 0) {
									var contact_name = r.message[0].parent;
									dialog.set_value('contact', contact_name);

									frappe.call({
										method: 'frappe.client.get_value',
										args: {
											doctype: 'Contact',
											filters: { name: contact_name },
											fieldname: ['mobile_no']
										},
										callback: function(r2) {
											if (r2.message && r2.message.mobile_no) {
												dialog.set_value('mobile_no', r2.message.mobile_no);
											}
										}
									});
								}
							}
						});
					}
				});
			}
		});
	}
});
