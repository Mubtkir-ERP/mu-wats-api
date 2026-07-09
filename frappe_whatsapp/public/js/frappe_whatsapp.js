$(document).on('app_ready', function () {
        // ═══════════════════════════════════════════════
        // WhatsApp Connection Status Indicator in Navbar
        // ═══════════════════════════════════════════════
        (function() {
                var $indicator = null;

                function inject_indicator() {
                        // Already injected? stop retrying.
                        if (document.getElementById('wa-status-indicator')) {
                                return true;
                        }

                        // Try several selectors for the navbar's right-hand list,
                        // which differ across Frappe versions.
                        var $navbar = $('header.navbar .navbar-nav.ml-auto, '
                                + 'header.navbar ul.navbar-nav:last, '
                                + '.navbar .navbar-right, '
                                + '.navbar .navbar-end, '
                                + '.navbar-nav:last');

                        if (!$navbar.length) {
                                return false;  // navbar not ready yet
                        }

                        $indicator = $(
                                '<li class="nav-item" title="WhatsApp Status" id="wa-status-indicator">'
                                + '<a class="nav-link" href="#" style="padding:10px 8px;display:flex;align-items:center;">'
                                + '<span id="wa-status-dot" style="width:12px;height:12px;border-radius:50%;'
                                + 'background:#ccc;display:inline-block;box-shadow:0 0 0 2px rgba(0,0,0,0.1);'
                                + 'transition:background 0.3s, box-shadow 0.3s;"></span>'
                                + '</a></li>'
                        );

                        $($navbar[0]).prepend($indicator);
                        bind_click();
                        update_status();
                        return true;
                }

                function bind_click() {
                        $indicator.find('a').on('click', function(e) {
                        e.preventDefault();
                        var instance_name = $indicator.data('instance');
                        if (instance_name) {
                                frappe.set_route('Form', 'Whatsapp Instance', instance_name);
                        } else {
                                frappe.set_route('List', 'Whatsapp Instance');
                        }
                        });
                }

                var previous_status = null;

                function update_status() {
                        frappe.call({
                                method: 'frappe_whatsapp.api.get_user_instance_status',
                                async: true,
                                callback: function(r) {
                                        if (!r.message) {
                                                set_dot('#ccc', 'No WhatsApp instance linked');
                                                return;
                                        }

                                        var status = r.message.status;
                                        var instance = r.message.instance_name;
                                        $indicator.data('instance', instance);

                                        if (status === 'Connected') {
                                                set_dot('#22c55e', 'WhatsApp Connected — ' + instance);
                                        } else if (status === 'Connecting') {
                                                set_dot('#f59e0b', 'WhatsApp Connecting — ' + instance);
                                        } else {
                                                set_dot('#ef4444', 'WhatsApp Disconnected — ' + instance);
                                        }

                                        // Alert when connection drops
                                        if (previous_status === 'Connected' && status !== 'Connected') {
                                                frappe.show_alert({
                                                        message: __('⚠ WhatsApp disconnected! Click the status dot to reconnect.'),
                                                        indicator: 'red'
                                                }, 15);
                                        }

                                        previous_status = status;
                                },
                                error: function() {
                                        set_dot('#ccc', 'WhatsApp status unavailable');
                                }
                        });
                }

                function set_dot(color, tooltip) {
                        var $dot = $('#wa-status-dot');
                        $dot.css({
                                'background': color,
                                'box-shadow': '0 0 0 3px ' + color + '33'
                        });
                        if ($indicator) {
                                $indicator.attr('title', tooltip);
                        }
                }

                // The navbar may not exist yet when app_ready fires. Retry every
                // 500ms (up to ~15s) until the indicator is injected, then poll
                // the status every 30s.
                var tries = 0;
                var injectTimer = setInterval(function() {
                        tries += 1;
                        if (inject_indicator() || tries > 30) {
                                clearInterval(injectTimer);
                        }
                }, 500);

                // Also try immediately.
                inject_indicator();

                setInterval(function() {
                        if (document.getElementById('wa-status-dot')) {
                                update_status();
                        }
                }, 30000);
        })();

        // ═══════════════════════════════════════════════
        // Send To WhatsApp menu item on all forms
        // ═══════════════════════════════════════════════
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

                                                // ═══════════════════════════════════════
                                                // Auto-fill Primary Contact and mobile_no
                                                // ═══════════════════════════════════════
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
                };
        })
});