frappe.ui.form.on('Bulk WhatsApp Message', {
    refresh: function(frm) {
        // Preview the final message per recipient before sending.
        frm.add_custom_button(__('Preview Messages'), function() {
            preview_bulk_messages(frm);
        });

        // Diagnostics: show the per-recipient send log (Evolution responses).
        if (frm.doc.send_log) {
            frm.add_custom_button(__('View Send Log'), function() {
                const d = new frappe.ui.Dialog({
                    title: __('Send Log'),
                    size: 'large',
                    fields: [{ fieldtype: 'HTML', fieldname: 'log' }],
                });
                d.get_field('log').$wrapper.html(
                    `<pre style="max-height:60vh;overflow:auto;white-space:pre-wrap;` +
                    `word-break:break-word;font-size:12px;background:var(--fg-color,#f7f7f7);` +
                    `padding:12px;border-radius:8px;">${frappe.utils.escape_html(frm.doc.send_log)}</pre>`
                );
                d.show();
            });
        }

        // Show batch status and trigger button if campaign is queued/cooling down
        if (frm.doc.docstatus === 1 && frm.doc.status === 'Queued' && frm.doc.next_batch_time) {
            frm.dashboard.set_headline_alert(
                __('Campaign in cool-down. Next batch scheduled at: {0}', [frm.doc.next_batch_time]),
                'orange'
            );
            frm.add_custom_button(__('Send Next Batch Now'), function() {
                frappe.confirm(
                    __('Do you want to send the next batch immediately without waiting for the scheduled cool-down time?'),
                    function() {
                        frappe.call({
                            method: 'frappe_whatsapp.utils.bulk_messaging.send_next_batch_now',
                            args: { name: frm.doc.name },
                            freeze: true,
                            callback: function(r) {
                                if (r.message) {
                                    frappe.show_alert({
                                        message: __('Next batch queued for sending'),
                                        indicator: 'green'
                                    });
                                    frm.reload_doc();
                                }
                            }
                        });
                    }
                );
            }).addClass('btn-primary');
        }

        // Add progress bar
        if(frm.doc.docstatus === 1 && frm.doc.status != 'Draft') {
            frm.add_custom_button(__('Check Progress'), function() {
                frappe.call({
                    method: 'frappe_whatsapp.utils.bulk_messaging.get_progress',
                    args: {
                        name: frm.doc.name
                    },
                    callback: function(r) {
                        if(r.message) {
                            let progress = r.message;
                            let html = `
                                <div class="progress" style="height: 20px;">
                                    <div class="progress-bar bg-success" role="progressbar" 
                                        style="width: ${progress.percent}%;" 
                                        aria-valuenow="${progress.percent}" 
                                        aria-valuemin="0" 
                                        aria-valuemax="100">
                                        ${Math.round(progress.percent)}%
                                    </div>
                                </div>
                                <div class="mt-2">
                                    <span class="badge badge-success">Sent: ${progress.sent}</span>
                                    <span class="badge badge-danger ml-2">Failed: ${progress.failed}</span>
                                    <span class="badge badge-warning ml-2">Queued: ${progress.queued}</span>
                                    <span class="badge badge-info ml-2">Total: ${progress.total}</span>
                                </div>
                            `;
                            if (progress.completed_batches) {
                                html += `<div class="mt-2 text-muted small">Completed Batches: ${progress.completed_batches}</div>`;
                            }
                            if (progress.next_batch_time) {
                                html += `<div class="mt-1 text-primary small">Next Batch Time: ${progress.next_batch_time}</div>`;
                            }
                            
                            frappe.msgprint({
                                title: __('Message Progress'),
                                indicator: 'blue',
                                message: html
                            });
                        }
                    }
                });
            });
            
            // Add retry button
            frm.add_custom_button(__('Retry Failed Messages'), function() {
                frappe.call({
                    method: 'frappe_whatsapp.utils.bulk_messaging.retry_failed',
                    args: {
                        name: frm.doc.name
                    },
                    callback: function(r) {
                        if(r.message) {
                            frm.reload_doc();
                        }
                    }
                });
            }).addClass('btn-danger');
        }
    },
    validate: function(frm) {
        if(frm.doc.recipient_type == 'Individual' && (!frm.doc.recipients || frm.doc.recipients.length === 0)) {
            frappe.throw(__('Please add at least one recipient'));
            return false;
        }
        
        if(frm.doc.recipient_type == 'Recipient List' && !frm.doc.recipient_list) {
            frappe.throw(__('Please select a recipient list'));
            return false;
        }
        
        if(frm.doc.use_template) {
            let mode = frm.doc.template_mode || 'Single Template';
            if (mode === 'Single Template' && !frm.doc.template) {
                frappe.throw(__('Please select a template'));
                return false;
            }
            if (mode === 'Rotate Multiple Templates' && (!frm.doc.rotating_templates || frm.doc.rotating_templates.length === 0)) {
                frappe.throw(__('Please add at least one template in Rotating Templates'));
                return false;
            }
        }
        
        if(!frm.doc.use_template && !frm.doc.message_content && !frm.doc.attach) {
            frappe.throw(__('Please enter message content or attach a file'));
            return false;
        }
        
        if(frm.doc.enable_batch_sending && (!frm.doc.batch_size || frm.doc.batch_size <= 0)) {
            frappe.throw(__('Batch size must be greater than 0'));
            return false;
        }

        return true;
    }
});

function preview_bulk_messages(frm) {
    frappe.call({
        doc: frm.doc,
        method: "preview_messages",
        args: { limit: 25 },
        freeze: true,
        freeze_message: __("Building preview..."),
        callback: function(r) {
            const rows = r.message || [];
            if (!rows.length) {
                frappe.msgprint(__("No recipients to preview. Add recipients first."));
                return;
            }
            const esc = frappe.utils.escape_html;
            let body = rows.map(function(row, i) {
                let tplBadge = row.template ? `<span class="badge badge-light ml-2">Template: ${esc(row.template)}</span>` : '';
                return `
                    <div style="border:1px solid var(--border-color,#e0e0e0);border-radius:8px;
                                padding:10px 12px;margin-bottom:8px;">
                        <div style="font-weight:600;margin-bottom:6px;">
                            ${i + 1}. ${esc(row.name || "")}
                            <span style="color:var(--text-muted,#888);font-weight:400;">${esc(row.mobile || "")}</span>
                            ${tplBadge}
                        </div>
                        <div dir="auto" style="white-space:pre-wrap;line-height:1.6;">${esc(row.message || "")}</div>
                    </div>`;
            }).join("");

            const note = rows.length >= 25
                ? `<p style="color:var(--text-muted,#888);">${__("Showing the first 25 recipients.")}</p>`
                : "";

            const d = new frappe.ui.Dialog({
                title: __("Message Preview") + ` (${rows.length})`,
                size: "large",
                fields: [{ fieldtype: "HTML", fieldname: "preview" }],
            });
            d.get_field("preview").$wrapper.html(note + body);
            d.show();
        }
    });
}
