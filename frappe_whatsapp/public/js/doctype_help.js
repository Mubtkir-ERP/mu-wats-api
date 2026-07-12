// Copyright (c) 2026, Shridhar Patil and contributors
// For license information, please see license.txt
//
// Renders a bilingual (Arabic + English) usage explanation at the BOTTOM of
// every WhatsApp doctype form. The text is centralised here so all doctypes
// stay consistent and no schema migration is required.

frappe.provide("frappe_whatsapp");

// doctype name -> { en, ar }. Keep the wording short and practical: what the
// screen is for and when to use it.
frappe_whatsapp.DOCTYPE_HELP = {
	"Whatsapp Instance": {
		en: "A single WhatsApp connection hosted on an Evolution server. Create the instance, scan the QR code to connect a phone number, and monitor its live status. Each user may own one instance.",
		ar: "اتصال واتساب واحد مُستضاف على خادم Evolution. أنشئ النسخة، وامسح رمز QR لربط رقم الهاتف، وراقب حالة الاتصال المباشرة. لكل مستخدم نسخة واحدة فقط.",
	},
	"Evolution Server": {
		en: "The Evolution API server that hosts your WhatsApp instances. Set its base URL and API key, use \"Test Connection\" to verify reachability, and \"Sync Instances\" to reconcile local records with the server.",
		ar: "خادم Evolution API الذي يستضيف نسخ الواتساب. اضبط عنوان الرابط ومفتاح الـ API، واستخدم \"اختبار الاتصال\" للتحقق من الوصول، و\"مزامنة النسخ\" لمطابقة السجلات المحلية مع الخادم.",
	},
	"Evolution Phone Settings": {
		en: "Central place to link users to their WhatsApp instance and manage phone connection settings.",
		ar: "المكان المركزي لربط المستخدمين بنسخة الواتساب الخاصة بهم وإدارة إعدادات اتصال الهاتف.",
	},
	"WhatsApp Notification": {
		en: "Define automated WhatsApp messages triggered by document events (e.g. a new invoice) or on a schedule. Choose the template, the reference doctype and the condition that sends it.",
		ar: "عرّف رسائل واتساب التلقائية التي تُرسَل عند أحداث المستندات (مثل فاتورة جديدة) أو حسب جدول زمني. اختر القالب ونوع المستند المرجعي والشرط الذي يُطلق الإرسال.",
	},
	"WhatsApp Templates": {
		en: "Message templates approved for WhatsApp. Build the header, body and buttons here, then reuse the template in notifications and manual sends.",
		ar: "قوالب الرسائل المعتمدة للواتساب. صمّم الترويسة والنص والأزرار هنا، ثم أعد استخدام القالب في الإشعارات والإرسال اليدوي.",
	},
	"WhatsApp Message": {
		en: "A log of every individual WhatsApp message sent or received, with its delivery status (Sent, Delivered, Read, Failed). Records are created automatically.",
		ar: "سجل لكل رسالة واتساب مُرسَلة أو مُستقبَلة، مع حالة التسليم (مُرسَلة، مُوصّلة، مقروءة، فاشلة). تُنشأ السجلات تلقائياً.",
	},
	"Bulk WhatsApp Message": {
		en: "Send a campaign to many recipients at once. Pick a recipient list, choose the template or content, and control the sending rate to reduce the risk of blocking.",
		ar: "أرسل حملة إلى عدة مستلمين دفعة واحدة. اختر قائمة المستلمين والقالب أو المحتوى، وتحكّم في معدّل الإرسال لتقليل خطر الحظر.",
	},
	"WhatsApp Recipient List": {
		en: "A reusable list of recipients for bulk campaigns. Add numbers manually or pull them from customers, then reference this list when sending.",
		ar: "قائمة مستلمين قابلة لإعادة الاستخدام للحملات الجماعية. أضف الأرقام يدوياً أو اسحبها من العملاء، ثم استخدم هذه القائمة عند الإرسال.",
	},
	"WhatsApp Settings": {
		en: "Central configuration for the WhatsApp app: default print formats per document type, attachment limits, and log retention. These settings apply account-wide.",
		ar: "الإعدادات المركزية لتطبيق الواتساب: نماذج الطباعة الافتراضية لكل نوع مستند، وحدود المرفقات، ومدة الاحتفاظ بالسجلات. تنطبق هذه الإعدادات على الحساب بالكامل.",
	},
	"WhatsApp Notification Log": {
		en: "A history of automated notifications that were triggered, showing what was sent, to whom, and the result. Use it to audit and troubleshoot notifications.",
		ar: "سجل تاريخي للإشعارات التلقائية التي تم إطلاقها، يوضّح ما أُرسِل ولمن والنتيجة. استخدمه لمراجعة الإشعارات وتشخيص مشاكلها.",
	},
};

frappe_whatsapp.render_doctype_help = function (frm) {
	const help = frappe_whatsapp.DOCTYPE_HELP[frm.doctype];
	if (!help) {
		return;
	}

	// The form body where sections are rendered. Fall back gracefully across
	// Frappe versions.
	const $body =
		(frm.layout && frm.layout.wrapper && $(frm.layout.wrapper)) ||
		(frm.$wrapper && frm.$wrapper.find(".form-layout")) ||
		$(frm.wrapper).find(".form-layout");
	if (!$body || !$body.length) {
		return;
	}

	// Re-render cleanly on every refresh (avoid stacking duplicates).
	$body.find(".wa-doctype-help").remove();

	const en = frappe.utils.escape_html(help.en);
	const ar = frappe.utils.escape_html(help.ar);

	const $card = $(`
		<div class="wa-doctype-help" style="
			margin: 24px 0 8px 0;
			padding: 16px 18px;
			border: 1px solid var(--border-color, #e0e0e0);
			border-left: 4px solid #25D366;
			border-radius: 10px;
			background: var(--fg-color, #fafafa);">
			<div style="display:flex; align-items:center; gap:8px; margin-bottom:10px;">
				<i class="fa fa-info-circle" style="color:#25D366;"></i>
				<span style="font-weight:600;">${__("About this screen")} · ${__("عن هذه الشاشة")}</span>
			</div>
			<div dir="rtl" style="text-align:right; margin-bottom:8px; line-height:1.7;">${ar}</div>
			<div dir="ltr" style="text-align:left; color:var(--text-muted, #6c7680); line-height:1.6;">${en}</div>
		</div>
	`);

	$body.append($card);
};

// Register a refresh handler once per doctype at load time (not on every route
// change) so handlers never stack.
Object.keys(frappe_whatsapp.DOCTYPE_HELP).forEach(function (doctype) {
	frappe.ui.form.on(doctype, {
		refresh: function (frm) {
			frappe_whatsapp.render_doctype_help(frm);
		},
	});
});
