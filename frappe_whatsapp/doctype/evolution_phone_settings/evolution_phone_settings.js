// Copyright (c) 2025, Shridhar Patil and contributors
// For license information, please see license.txt

// frappe.ui.form.on("Evolution Phone Settings", {
// 	refresh(frm) {

// 	},
// });
frappe.ui.form.on("Evolution Phone Settings", {
    refresh(frm) {
        // زر الاتصال وعرض QR
        frm.add_custom_button(__('🔗 اتصال / عرض QR'), function () {
            frm.call({
                method: "frappe_whatsapp.frappe_whatsapp.doctype.evolution_phone_settings.evolution_phone_settings.get_qr_code",
                args: { name: frm.doc.name },
                callback: function (r) {
                    if (r.message) {
                        const data = r.message;

                        if (data.status === "open") {
                            frappe.msgprint({
                                title: __('حالة الاتصال'),
                                indicator: 'green',
                                message: `<div style="text-align:center; padding:20px;">
                                    <i class="fa fa-check-circle" style="font-size:60px; color:green;"></i>
                                    <h3 style="color:green; margin-top:15px;">✅ الرقم متصل بالفعل</h3>
                                    <p>Instance: <b>${frm.doc.instance_name}</b></p>
                                </div>`
                            });
                        } else if (data.base64) {
                            // إزالة prefix إذا موجود
                            let qr_img = data.base64;
                            if (!qr_img.startsWith("data:image")) {
                                qr_img = "data:image/png;base64," + qr_img;
                            }
                            frappe.msgprint({
                                title: __('امسح هذا الـ QR Code بتطبيق واتساب'),
                                indicator: 'blue',
                                message: `
                                    <div style="text-align:center; padding:10px;">
                                        <p style="color:#555; margin-bottom:10px;">
                                            افتح واتساب على جوالك ← الإعدادات ← الأجهزة المرتبطة ← ربط جهاز
                                        </p>
                                        <img src="${qr_img}" 
                                             style="width:280px; height:280px; border:3px solid #25D366; border-radius:12px; padding:5px;" />
                                        <p style="margin-top:10px; color:#888; font-size:12px;">
                                            ⏱ صالح لمدة 60 ثانية — اضغط الزر مجدداً إذا انتهت المدة
                                        </p>
                                    </div>`
                            });
                        } else {
                            frappe.msgprint({
                                title: __('خطأ'),
                                indicator: 'red',
                                message: data.message || __('تعذّر جلب QR Code، تحقق من إعدادات الـ API')
                            });
                        }
                    }
                }
            });
        }, __("WhatsApp"));

        // زر حالة الاتصال
        frm.add_custom_button(__('📡 حالة الاتصال'), function () {
            frm.call({
                method: "frappe_whatsapp.frappe_whatsapp.doctype.evolution_phone_settings.evolution_phone_settings.get_connection_state",
                args: { name: frm.doc.name },
                callback: function (r) {
                    if (r.message) {
                        const state = r.message.state || "unknown";
                        const color = state === "open" ? "green" : "orange";
                        const icon  = state === "open" ? "✅" : "⚠️";
                        frappe.msgprint({
                            title: __('حالة الاتصال'),
                            indicator: color,
                            message: `<b>${icon} الحالة: ${state}</b><br>Instance: ${frm.doc.instance_name}`
                        });
                    }
                }
            });
        }, __("WhatsApp"));

        // زر قطع الاتصال
        frm.add_custom_button(__('🔌 قطع الاتصال (Logout)'), function () {
            frappe.confirm(
                __('هل تريد قطع اتصال واتساب لهذا الرقم؟'),
                () => {
                    frm.call({
                        method: "frappe_whatsapp.frappe_whatsapp.doctype.evolution_phone_settings.evolution_phone_settings.logout_instance",
                        args: { name: frm.doc.name },
                        callback: function (r) {
                            frappe.msgprint(__('تم قطع الاتصال بنجاح'));
                        }
                    });
                }
            );
        }, __("WhatsApp"));
    }
});
