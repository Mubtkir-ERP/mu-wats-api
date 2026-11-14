// Copyright (c) 2026, Shridhar Patil and contributors
// For license information, please see license.txt
//
// Renders a detailed bilingual (Arabic + English) usage guide at the BOTTOM of
// every WhatsApp doctype form. The text is centralised here so all doctypes
// stay consistent and no schema migration is required. Each entry explains what
// the screen is for, when to use it, and the key steps/fields — enough that a
// client reading it knows how to operate the screen.

frappe.provide("frappe_whatsapp");

// doctype name -> { en: [paragraphs], ar: [paragraphs] }
frappe_whatsapp.DOCTYPE_HELP = {
	"Whatsapp Instance": {
		en: [
			"This screen represents a single WhatsApp connection (one phone number) hosted on an Mubtkir API server. Each user may own one instance.",
			"How to use it: 1) Pick the Mubtkir API Server, then save — the instance name is generated automatically. 2) Click \"Create Instance\" to register it on Mubtkir API. 3) Click \"Show QR Code\" and scan it from WhatsApp on the phone (Settings → Linked Devices → Link a Device). The dialog closes automatically once connected.",
			"The coloured status dot shows the live state (green = connected, orange = connecting, red = disconnected). Use \"Check Status\" to refresh it, \"Disconnect\" to log the phone out, and \"Sync with Mubtkir API\" to reconcile with the server — if the instance was deleted on Mubtkir API, the record is kept and reset so you can re-create it.",
		],
		ar: [
			"هذه الشاشة تمثّل اتصال واتساب واحد (رقم هاتف واحد) مُستضاف على خادم Mubtkir API. لكل مستخدم نسخة واحدة فقط.",
			"طريقة الاستخدام: ١) اختر خادم Mubtkir API ثم احفظ — يُولَّد اسم النسخة تلقائياً. ٢) اضغط «Create Instance» لتسجيلها على Mubtkir API. ٣) اضغط «Show QR Code» وامسح الرمز من واتساب في الجوال (الإعدادات ← الأجهزة المرتبطة ← ربط جهاز). تُغلق النافذة تلقائياً عند نجاح الاتصال.",
			"نقطة الحالة الملوّنة توضّح الوضع المباشر (أخضر = متصل، برتقالي = يتصل، أحمر = منقطع). استخدم «Check Status» لتحديثها، و«Disconnect» لتسجيل خروج الجوال، و«Sync with Mubtkir API» لمطابقة الحالة مع الخادم — وإذا حُذفت النسخة من Mubtkir API يُحتفَظ بالسجل ويُعاد تعيينه لتتمكن من إعادة إنشائه.",
		],
	},
	"Evolution Server": {
		en: [
			"This screen defines an Mubtkir API server — the engine that actually hosts and runs your WhatsApp instances. You can add more than one server.",
			"How to use it: enter the server's Base URL (e.g. https://evo.example.com) and its global API Key, then save. These credentials are visible to System Managers only.",
			"Use \"Test Connection\" to confirm the server is reachable (the status field updates to Online/Offline). Use \"Sync Instances\" to reconcile every local instance on this server against Mubtkir API — any instance deleted on the server is reset locally so it can be re-created.",
		],
		ar: [
			"هذه الشاشة تُعرّف خادم Mubtkir API — وهو المحرّك الذي يستضيف ويشغّل نسخ الواتساب فعلياً. يمكنك إضافة أكثر من خادم.",
			"طريقة الاستخدام: أدخل الرابط الأساسي للخادم (مثال: https://evo.example.com) ومفتاح الـ API العام، ثم احفظ. بيانات الاعتماد هذه مرئية لمديري النظام فقط.",
			"استخدم «Test Connection» للتأكد من إمكانية الوصول للخادم (يتحدّث حقل الحالة إلى Online/Offline). واستخدم «Sync Instances» لمطابقة كل النسخ المحلية على هذا الخادم مع Mubtkir API — وأي نسخة حُذفت من الخادم يُعاد تعيينها محلياً لتتمكن من إعادة إنشائها.",
		],
	},
	"Evolution Phone Settings": {
		en: [
			"Central screen for linking users to their WhatsApp instance and managing phone connection settings in one place.",
			"How to use it: choose the user and the instance/phone to associate them, then save. Once linked, that user's documents (invoices, statements, notifications) are sent automatically from their own connected number.",
		],
		ar: [
			"شاشة مركزية لربط المستخدمين بنسخة الواتساب الخاصة بهم وإدارة إعدادات اتصال الهاتف في مكان واحد.",
			"طريقة الاستخدام: اختر المستخدم والنسخة/الرقم لربطهما، ثم احفظ. بعد الربط تُرسَل مستندات هذا المستخدم (الفواتير، كشوف الحساب، الإشعارات) تلقائياً من رقمه المتصل.",
		],
	},
	"WhatsApp Notification": {
		en: [
			"This screen automates WhatsApp messages so they are sent without any manual work — either when a document event happens (e.g. an invoice is submitted) or on a schedule (e.g. daily/monthly).",
			"How to use it: 1) Choose the Notification Type (DocType Event or Scheduler Event). 2) Pick the Reference Document Type and the exact event/timing. 3) Select the Template to send. 4) Set how the recipient's phone number is resolved (from the linked contact or a field). 5) Optionally add a Condition (simple Python) so it only sends when your rule is true.",
			"Tip: use the Condition Examples in the Help section to avoid duplicate sends and to target specific customers. Enable the notification when you are ready for it to start firing.",
		],
		ar: [
			"هذه الشاشة تُؤتمت رسائل الواتساب لتُرسَل بدون أي عمل يدوي — إمّا عند وقوع حدث على مستند (مثل ترحيل فاتورة) أو حسب جدول زمني (يومي/شهري).",
			"طريقة الاستخدام: ١) اختر نوع الإشعار (حدث مستند أو حدث مجدول). ٢) حدّد نوع المستند المرجعي والحدث/التوقيت بدقة. ٣) اختر القالب المراد إرساله. ٤) اضبط طريقة الحصول على رقم جوال المستلم (من جهة الاتصال المرتبطة أو من حقل). ٥) اختيارياً أضف شرطاً (Python بسيط) ليُرسَل فقط عند تحقّق قاعدتك.",
			"نصيحة: استعن بأمثلة الشروط في قسم المساعدة لتجنّب الإرسال المكرر ولاستهداف عملاء محددين. فعّل الإشعار عندما تكون جاهزاً لبدء إطلاقه.",
		],
	},
	"WhatsApp Templates": {
		en: [
			"This screen holds the message templates approved for WhatsApp. A template is the reusable layout (header, body text, footer and buttons) used by notifications and manual sends.",
			"How to use it: write the Template Label, choose the Category and Language, and compose the Header/Body/Footer. Use placeholders and list the matching Field names so values are pulled from the document at send time.",
			"Tip: keep the field order identical to the template's parameters. Templates usually need approval before they can be used for outgoing messages.",
		],
		ar: [
			"هذه الشاشة تحتوي قوالب الرسائل المعتمدة للواتساب. القالب هو التصميم القابل لإعادة الاستخدام (الترويسة، النص، التذييل، والأزرار) الذي تستخدمه الإشعارات والإرسال اليدوي.",
			"طريقة الاستخدام: اكتب اسم القالب، واختر الفئة واللغة، وصمّم الترويسة/النص/التذييل. استخدم المتغيّرات وأدرِج أسماء الحقول المطابقة ليتم سحب القيم من المستند وقت الإرسال.",
			"نصيحة: حافظ على ترتيب الحقول مطابقاً لمتغيّرات القالب تماماً. عادةً تحتاج القوالب إلى اعتماد قبل استخدامها للرسائل الصادرة.",
		],
	},
	"WhatsApp Message": {
		en: [
			"This screen is the log of every individual WhatsApp message sent or received, together with its delivery status (Sent, Delivered, Read, Failed, Received).",
			"How to use it: records are created automatically — you normally open one to review its content, status, timestamps and the reference document it relates to. Use it to audit deliveries and investigate any failures.",
		],
		ar: [
			"هذه الشاشة هي سجل كل رسالة واتساب مُرسَلة أو مُستقبَلة، مع حالة تسليمها (مُرسَلة، مُوصّلة، مقروءة، فاشلة، واردة).",
			"طريقة الاستخدام: تُنشأ السجلات تلقائياً — وعادةً تفتح السجل لمراجعة محتواه وحالته وأوقاته والمستند المرجعي المرتبط به. استخدمها لمراجعة عمليات التسليم وتشخيص أي إخفاقات.",
		],
	},
	"Bulk WhatsApp Message": {
		en: [
			"This screen sends bulk WhatsApp campaigns to multiple recipients at once — for marketing, announcements, customer follow-ups or reminders, equipped with advanced anti-ban protection and warming algorithms.",
			"Key Steps: 1) Set a Title and select the Send-From WhatsApp instance. 2) Choose recipients: Individual (add rows) or a saved Recipient List. 3) Write your message or turn on «Use Template». 4) Configure the random delay and batch cooling settings to prevent phone number bans. 5) Optionally attach an image/file and set a Scheduled Time, then Submit. Always use «Preview Messages» to verify the generated text before sending.",
			"Anti-Ban & Batch Warming System (Cool-down):",
			"• Random Delay: Set Min and Max delay in seconds (e.g. 30 to 150 seconds) to emulate natural human messaging behavior.",
			"• Batch Sending & Cool-down: Specify batch size (e.g. 20 messages) and wait time in hours (e.g. 24 hours). The system sends the batch, enters a cool-down pause, and the background scheduler automatically triggers the next batch when the time arrives. You can also click «Send Next Batch Now» to resume immediately.",
			"• Gradual Warm-up: Automatically scales up batch size (e.g. +10 messages every 2 batches) up to a safe ceiling (e.g. 50 messages/day) to build number reputation safely.",
			"Template Rotation & Message Variations (Spintax):",
			"• Template Mode: Choose «Single Template», «Rotate Multiple Templates» (cycles through multiple templates across recipients to break repetitive spam fingerprint patterns), or «Per Recipient».",
			"• Spintax Syntax: In plain text messages, use pipe-separated variations inside curly braces: {Hello|Hi|Greetings} {name}, to generate unique text variations per contact.",
			"• Dynamic Placeholders: Use {name} for recipient's name, {mobile} for phone number, or {{1}}, {{2}}... for template parameters (Common or Unique from Recipient Data).",
			"Media & Image Attachments:",
			"• Attach an image (PNG, JPG, WEBP), PDF, video or audio file. When sending via Evolution API, the image is delivered directly with your message text or template body displayed as an image caption.",
		],
		ar: [
			"هذه الشاشة تُرسل حملات رسائل واتساب جماعية إلى عدة مستلمين دفعة واحدة — للتسويق، الإعلانات، أو التذكيرات، مع نظام متقدم للحماية من الحظر والتسخين التدريجي للرقم.",
			"الخطوات الأساسية: ١) اضبط عنواناً للحملة وحدد نسخة واتساب المُرسِلة. ٢) اختر المستلمين: فردي (إضافة أرقام يدوياً) أو قائمة مستلمين محفوظة. ٣) حدد نص الرسالة أو فعّل «استخدام قالب». ٤) اضبط الفاصل العشوائي بين الرسائل وإعدادات الدفعات لحماية الرقم. ٥) اختيارياً أرفق صورة أو ملف واضبط وقتاً مجدولاً ثم رحّل المستند. استخدم «معاينة الرسائل» دائماً للتأكد من النصوص النهائية قبل البدء.",
			"الحماية من الحظر والدفعات (Anti-Ban & Batch Cooling):",
			"• الفاصل العشوائي (Random Delay): حدد أدنى وأقصى تأخير بالثواني (مثلاً 30 إلى 150 ثانية) لمحاكاة الإرسال البشري الطبيعي.",
			"• الإرسال على دفعات والتبريد (Batch Sending): حدد عدد الرسائل في كل دفعة (مثلاً 20 رسالة) ومدة الانتظار بالساعات (مثلاً 24 ساعة). يقوم النظام بإرسال الدفعة ثم يتوقف مؤقتاً للتبريد، ويستأنف المجدول تلقائياً إرسال الدفعة التالية عند حلول الوقت دون أي تدخل يدوي، مع إمكانية الضغط على «إرسال الدفعة التالية الآن» لتجاوز الانتظار.",
			"• التسخين التدريجي (Gradual Warm-up): خيار أوتوماتيكي لزيادة حجم الدفعة (مثلاً +10 رسائل كل دفعتين) حتى الوصول للحد الأقصى (مثلاً 50 رسالة/يوم) لتسخين الأرقام الجديدة بأمان تام.",
			"تدوير القوالب وتنويع النصوص (Template Rotation & Spintax):",
			"• نمط القالب (Template Mode): يمكنك اختيار «قالب موحد» للجميع، أو «تدوير عدة قوالب» حيث يتبدل النظام بين عدة قوالب بالتناوب على الأرقام لكسر نمط الرسائل المتطابقة ومنع رصد السبام، أو «قالب مخصص لكل مستلم».",
			"• نصوص تبادلية (Spintax): في الرسائل العادية، اكتب صيغاً بديلة بين أقواس وعلامة | مثل: {مرحباً|أهلاً|السلام عليكم} {name}، ليختار النظام صياغة عشوائية مختلفة لكل رقم.",
			"• المتغيرات المخصصة: استخدم {name} لاسم العميل، و {mobile} لرقم هاتفه، أو {{1}} و {{2}} لقوالب واتساب سواء بصيغة مشتركة للجميع أو فريدة من حقل Recipient Data.",
			"إرفاق الصور والوسائط (Media Attachments):",
			"• يمكنك إرفاق صورة (PNG, JPG, WEBP) أو ملف PDF أو فيديو؛ وعند الإرسال عبر Evolution API تُرسل الصورة وتصل للمستلم مباشرة في الواتساب مع ظهور نص الرسالة أو القالب كشرح توضيحي (Caption) تحت الصورة.",
		],
	},
	"WhatsApp Contact Puller": {
		en: [
			"This screen extracts contacts and chat conversations directly from your connected WhatsApp instance, segments them by engagement tier (A/B/C), and exports them into recipient lists or Excel.",
			"How to use it: 1) Select the Source Instance and Source Type (Chats or Contacts). 2) Configure history depth and filters (e.g. active within days, minimum inbound messages, keywords). 3) Click «Pull Numbers». 4) Review the quality score and select numbers to save into a Target Recipient List or export to Excel.",
		],
		ar: [
			"هذه الشاشة تسحب جهات الاتصال ومحادثات الدردشة مباشرة من نسخة واتساب المتصلة، وتصنفها حسب درجة التفاعل إلى فئات (A/B/C)، وتتيح حفظها في قوائم مستلمين أو تصديرها إلى Excel.",
			"طريقة الاستخدام: ١) حدد النسخة المصدر ونوع المصدر (المحادثات أو جهات الاتصال). ٢) اضبط عمق السجل وفلاتر التصفية (مثل التفاعل خلال أيام محددة، الحد الأدنى للرسائل الواردة، الكلمات المفتاحية). ٣) اضغط «سحب الأرقام». ٤) راجع مؤشر جودة القائمة وحدد الأرقام لحفظها في «قائمة المستلمين المستهدفة» أو تصديرها لملف إكسل.",
		],
	},
	"WhatsApp Recipient List": {
		en: [
			"This screen builds a reusable list of recipients that bulk campaigns can target, so you don't re-enter numbers each time.",
			"How to use it: give the list a name, then add recipients manually or import them from a DocType (e.g. Customers) by choosing the mobile-number field and optional name field, with filters. Use \"Validate Recipients\" to check the numbers before sending.",
		],
		ar: [
			"هذه الشاشة تبني قائمة مستلمين قابلة لإعادة الاستخدام تستهدفها الحملات الجماعية، حتى لا تُعيد إدخال الأرقام في كل مرة.",
			"طريقة الاستخدام: أعطِ القائمة اسماً، ثم أضِف المستلمين يدوياً أو استوردهم من مستند (مثل العملاء) باختيار حقل رقم الجوال وحقل الاسم الاختياري مع المرشّحات. استخدم «Validate Recipients» للتحقق من الأرقام قبل الإرسال.",
		],
	},
	"WhatsApp Settings": {
		en: [
			"This is the central configuration for the whole WhatsApp app; the settings apply account-wide.",
			"How to use it: set the default Mubtkir API server used when creating new instances, the default Print Format per document type (for attachments), the maximum attachment size, and how many days message history is kept before cleanup. Adjust these once to match your policy.",
		],
		ar: [
			"هذه هي الإعدادات المركزية لتطبيق الواتساب بالكامل؛ وتنطبق على الحساب كله.",
			"طريقة الاستخدام: اضبط خادم Mubtkir API الافتراضي المستخدم عند إنشاء نسخ جديدة، ونموذج الطباعة الافتراضي لكل نوع مستند (للمرفقات)، وأقصى حجم للمرفق، وعدد الأيام للاحتفاظ بسجل الرسائل قبل التنظيف. اضبطها مرة واحدة لتطابق سياستك.",
		],
	},
	"WhatsApp Scheduled Report": {
		en: [
			"This screen sends an ERPNext report automatically on a schedule (daily/weekly/monthly) as a WhatsApp file.",
			"How to use it: 1) Pick the Report and the Format (PDF, Excel or CSV). 2) Optionally add report Filters as JSON. 3) Choose the recipient by Party Type + Party (their mobile is taken from the linked contact) or enter a mobile override. 4) Set the schedule and Send-From instance. Use \"Send Now\" to test immediately.",
		],
		ar: [
			"هذه الشاشة تُرسل تقرير ERPNext تلقائياً حسب جدول (يومي/أسبوعي/شهري) كملف واتساب.",
			"طريقة الاستخدام: ١) اختر التقرير والصيغة (PDF أو Excel أو CSV). ٢) اختيارياً أضف فلاتر التقرير بصيغة JSON. ٣) حدّد المستلم بنوع الطرف + الطرف المعني (يُؤخذ جواله من جهة الاتصال) أو أدخل رقم تجاوز. ٤) اضبط الجدولة والنسخة المُرسِلة. استخدم «إرسال الآن» للتجربة الفورية.",
		],
	},
	"WhatsApp Reminder Log": {
		en: [
			"This screen tracks the state of recurring reminders (from WhatsApp Notification's 'Repeat Until Condition Clears'). One row per document being reminded.",
			"Records are created and updated automatically: it shows the last reminder date, how many were sent, whether the condition cleared (e.g. the invoice was paid) and whether a thank-you was sent. You normally only view it to audit why a reminder did or didn't repeat.",
		],
		ar: [
			"هذه الشاشة تتتبّع حالة التذكيرات المتكررة (من خيار «التكرار حتى يتحقق الشرط» في إشعار واتساب). صف واحد لكل مستند يُذكَّر به.",
			"تُنشأ السجلات وتُحدَّث تلقائياً: تعرض تاريخ آخر تذكير، وعددها، وهل تحقّق الشرط (مثل سداد الفاتورة)، وهل أُرسلت رسالة الشكر. عادةً تفتحها فقط لمراجعة سبب تكرار التذكير أو توقّفه.",
		],
	},
	"WhatsApp Notification Log": {
		en: [
			"This screen is the history of automated notifications that were triggered — what was sent, to whom, and the result.",
			"How to use it: records are created automatically each time a WhatsApp Notification fires. Open an entry to audit a specific run or to troubleshoot why a notification did or did not send.",
		],
		ar: [
			"هذه الشاشة هي سجل الإشعارات التلقائية التي تم إطلاقها — ماذا أُرسِل، ولمن، والنتيجة.",
			"طريقة الاستخدام: تُنشأ السجلات تلقائياً في كل مرة يُطلَق فيها إشعار واتساب. افتح سجلاً لمراجعة عملية معيّنة أو لتشخيص سبب إرسال أو عدم إرسال إشعار.",
		],
	},
};

frappe_whatsapp.render_doctype_help = function (frm) {
	const help = frappe_whatsapp.DOCTYPE_HELP[frm.doctype];
	if (!help) {
		return;
	}

	const $body =
		(frm.layout && frm.layout.wrapper && $(frm.layout.wrapper)) ||
		(frm.$wrapper && frm.$wrapper.find(".form-layout")) ||
		$(frm.wrapper).find(".form-layout");
	if (!$body || !$body.length) {
		return;
	}

	// Re-render cleanly on every refresh (avoid stacking duplicates).
	$body.find(".wa-doctype-help").remove();

	const to_html = (paras) =>
		paras.map((p) => `<p style="margin:0 0 10px 0;">${frappe.utils.escape_html(p)}</p>`).join("");

	const ar_html = to_html(help.ar);
	const en_html = to_html(help.en);

	const $card = $(`
		<div class="wa-doctype-help" style="
			margin: 24px 0 8px 0;
			padding: 18px 20px;
			border: 1px solid var(--border-color, #e0e0e0);
			border-left: 4px solid #25D366;
			border-radius: 10px;
			background: var(--fg-color, #fafafa);">
			<div style="display:flex; align-items:center; gap:8px; margin-bottom:12px;">
				<i class="fa fa-info-circle" style="color:#25D366;"></i>
				<span style="font-weight:600; font-size:1.05em;">${__("How to use this screen")} · ${__("طريقة استخدام هذه الشاشة")}</span>
			</div>
			<div dir="rtl" style="text-align:right; line-height:1.9; margin-bottom:14px;">${ar_html}</div>
			<hr style="border:none; border-top:1px dashed var(--border-color, #ddd); margin:14px 0;">
			<div dir="ltr" style="text-align:left; color:var(--text-muted, #6c7680); line-height:1.7;">${en_html}</div>
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
