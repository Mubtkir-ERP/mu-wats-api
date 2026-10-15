"""Webhook."""
import frappe
import json
import hmac
import requests
from werkzeug.wrappers import Response
import frappe.utils
from frappe_whatsapp.utils.operations import claim_event
from frappe_whatsapp.utils import webhook_auth


@frappe.whitelist(allow_guest=True, methods=["GET", "POST"])
def webhook():
	"""Meta webhook."""
	if frappe.request.method == "GET":
		return get()
	return post()


def get():
	"""Get."""
	hub_challenge = frappe.form_dict.get("hub.challenge")
	webhook_verify_token = frappe.db.get_single_value(
		"WhatsApp Settings", "webhook_verify_token"
	)

	if not webhook_verify_token or not hmac.compare_digest(str(frappe.form_dict.get("hub.verify_token") or ""), str(webhook_verify_token)):
		frappe.throw("Verify token does not match")

	return Response(hub_challenge, status=200)

def post():
    data = webhook_auth.meta_payload()
    if not webhook_auth.event_once("Meta", "", data):
        return Response("OK", status=200)
    phone_id = frappe.db.get_single_value("WhatsApp Settings", "phone_id")
    for entry in data.get("entry") or []:
        for change in entry.get("changes") or []:
            incoming_phone = ((change.get("value") or {}).get("metadata") or {}).get("phone_number_id")
            if incoming_phone and phone_id and str(incoming_phone) != str(phone_id):
                continue
            _post_meta_item({"entry": [{"changes": [change]}]})
    return Response("OK", status=200)


def _post_meta_item(data):
	"""Post."""
	frappe.get_doc({
		"doctype": "WhatsApp Notification Log",
		"template": "Webhook",
		"meta_data": json.dumps({"source": "Meta", "event": "accepted"})
	}).insert(ignore_permissions=True)

	messages = []
	try:
		messages = data["entry"][0]["changes"][0]["value"].get("messages", [])
	except KeyError:
		messages = data["entry"]["changes"][0]["value"].get("messages", [])
	sender_profile_name = next(
		(
			contact.get("profile", {}).get("name")
			for entry in data.get("entry", [])
			for change in entry.get("changes", [])
			for contact in change.get("value", {}).get("contacts", [])
		),
		None,
	)


	if messages:
		for message in messages:
			mid = message.get("id")
			if not mid or not claim_event("incoming-meta", str(mid)):
				continue
			if frappe.db.exists("WhatsApp Message", {"message_id": mid, "channel": "Meta", "type": "Incoming"}):
				continue
			message_type = message['type']
			is_reply = bool((message.get('context') or {}).get('id'))
			reply_to_message_id = message['context']['id'] if is_reply else None
			if message_type == 'text':
				frappe.get_doc({
					"doctype": "WhatsApp Message",
					"type": "Incoming",
					"from": message['from'],
					"message": message['text']['body'],
					"message_id": message['id'],
					"reply_to_message_id": reply_to_message_id,
					"is_reply": is_reply,
					"content_type":message_type,
					"profile_name":sender_profile_name
				}).insert(ignore_permissions=True)
			elif message_type == 'reaction':
				frappe.get_doc({
					"doctype": "WhatsApp Message",
					"type": "Incoming",
					"from": message['from'],
					"message": message['reaction']['emoji'],
					"reply_to_message_id": message['reaction']['message_id'],
					"message_id": message['id'],
					"content_type": "reaction",
					"profile_name":sender_profile_name
				}).insert(ignore_permissions=True)
			elif message_type == 'interactive':
				frappe.get_doc({
					"doctype": "WhatsApp Message",
					"type": "Incoming",
					"from": message['from'],
                    "message": webhook_auth.interactive_text(message.get('interactive') or {}),
					"message_id": message['id'],
					"reply_to_message_id": reply_to_message_id,
					"is_reply": is_reply,
					"content_type": "flow",
					"profile_name":sender_profile_name
				}).insert(ignore_permissions=True)
			elif message_type in ["image", "audio", "video", "document"]:
				settings = frappe.get_doc("WhatsApp Settings", "WhatsApp Settings")
				filename, content = webhook_auth.fetch_meta_media(message[message_type].get("id"), settings)
				message_doc = frappe.get_doc({
					"doctype": "WhatsApp Message", "type": "Incoming", "channel": "Meta",
					"from": message['from'], "message_id": message['id'],
					"reply_to_message_id": reply_to_message_id, "is_reply": is_reply,
					"message": message[message_type].get("caption", ""),
					"content_type": message_type, "profile_name": sender_profile_name,
				}).insert(ignore_permissions=True)
				file = frappe.get_doc({
					"doctype": "File", "file_name": filename, "content": content, "is_private": 1,
					"attached_to_doctype": "WhatsApp Message", "attached_to_name": message_doc.name,
					"attached_to_field": "attach",
				}).insert(ignore_permissions=True)
				frappe.db.set_value("WhatsApp Message", message_doc.name, "attach", file.file_url)
			elif message_type == "button":
				frappe.get_doc({
					"doctype": "WhatsApp Message",
					"type": "Incoming",
					"from": message['from'],
					"message": message['button']['text'],
					"message_id": message['id'],
					"reply_to_message_id": reply_to_message_id,
					"is_reply": is_reply,
					"content_type": message_type,
					"profile_name":sender_profile_name
				}).insert(ignore_permissions=True)
			else:
				frappe.get_doc({
					"doctype": "WhatsApp Message",
					"type": "Incoming",
					"from": message['from'],
					"message_id": message['id'],
					"message": json.dumps(message.get(message_type) or {}, ensure_ascii=False),
					"content_type": message_type if message_type in ("location", "contact") else "text",
					"profile_name":sender_profile_name
				}).insert(ignore_permissions=True)

	update_status(data["entry"][0]["changes"][0])
	return

def update_status(data):
	"""Update status hook."""
	if data.get("field") == "message_template_status_update":
		update_template_status(data['value'])

	elif data.get("field") == "messages":
		update_message_status(data['value'])

def update_template_status(data):
	"""Update template status."""
	frappe.db.sql(
		"""UPDATE `tabWhatsApp Templates`
		SET status = %(event)s
		WHERE id = %(message_template_id)s""",
		data
	)

def update_message_status(data):
    from frappe_whatsapp.utils.messaging import can_advance_status, normalise_message_status
    for item in data.get("statuses") or []:
        message_id = item.get("id")
        status = normalise_message_status(item.get("status"))
        if not message_id or not status:
            continue
        name = frappe.db.get_value("WhatsApp Message", {"message_id": message_id, "channel": "Meta", "type": "Outgoing"}, "name")
        if not name:
            continue
        frappe.db.sql("SELECT name FROM `tabWhatsApp Message` WHERE name=%s FOR UPDATE", (name,))
        current = frappe.db.get_value("WhatsApp Message", name, "status")
        if can_advance_status(current, status):
            values = {"status": status}
            conversation = (item.get("conversation") or {}).get("id")
            if conversation:
                values["conversation_id"] = conversation
            frappe.db.set_value("WhatsApp Message", name, values)


# ---------------------------------------------------------------------------
# Evolution (Mubtkir API) inbound webhook
# ---------------------------------------------------------------------------






def _evolution_instance_name(data, item=None):
	"""Extract the local instance name from common Evolution v2 payload shapes."""
	candidates = [
		data.get("instance"),
		(data.get("data") or {}).get("instance") if isinstance(data.get("data"), dict) else None,
		item.get("instance") if isinstance(item, dict) else None,
	]
	for value in candidates:
		if isinstance(value, dict):
			value = value.get("instanceName") or value.get("name")
		if value:
			return str(value)
	return ""


def _validate_evolution_webhook(data):
    return webhook_auth.evolution(data, _evolution_instance_name(data))



@frappe.whitelist(allow_guest=True, methods=["GET", "POST"])
def evolution_webhook():
	"""Receive authenticated Evolution message/status events."""
	if frappe.request.method == "GET":
		return Response("OK", status=200)
	if frappe.request.method != "POST":
		return Response("Method Not Allowed", status=405)

	data = webhook_auth.payload()
	instance_name = _validate_evolution_webhook(data)
	if not webhook_auth.event_once("Evolution", instance_name, data):
		return Response("OK", status=200)

	# Only authenticated / known-instance payloads are persisted to the audit log.
	audit_data = dict(data)
	audit_data.pop("secret", None)
	frappe.get_doc({
		"doctype": "WhatsApp Notification Log",
		"template": "Evolution Webhook",
		"meta_data": json.dumps({"instance": instance_name, "event": data.get("event")}),
	}).insert(ignore_permissions=True)

	event = (data.get("event") or "").replace(".", "_").lower()
	payload = data.get("data") or {}
	items = payload if isinstance(payload, list) else [payload]

	if event == "messages_update":
		for item in items:
			_process_evolution_status(item, instance_name)
		return Response("OK", status=200)

	if event and event != "messages_upsert":
		return Response("OK", status=200)

	for item in items:
		_process_evolution_message(item, instance_name=instance_name)

	return Response("OK", status=200)


def _process_evolution_status(item, instance_name=None):
	from frappe_whatsapp.utils.messaging import can_advance_status, normalise_message_status

	if not isinstance(item, dict):
		return
	key = item.get("key", {}) or {}
	message_id = key.get("id") or item.get("keyId") or item.get("id")
	if not message_id:
		return
	status = normalise_message_status(item.get("status"))
	if status is None:
		status = normalise_message_status((item.get("update") or {}).get("status"))
	if not status:
		return

	name = frappe.db.get_value("WhatsApp Message", {"message_id": message_id, "channel": "Evolution", "send_from_instance": instance_name, "type": "Outgoing"}, "name")
	if not name:
		return
	frappe.db.sql("SELECT name FROM `tabWhatsApp Message` WHERE name=%s FOR UPDATE", (name,))
	current = frappe.db.get_value("WhatsApp Message", name, "status")
	if can_advance_status(current, status):
		frappe.db.set_value("WhatsApp Message", name, "status", status)


def _process_evolution_message(item, instance_name=None):
	"""Parse one inbound Evolution message and preserve its source instance."""
	if not isinstance(item, dict):
		return

	key = item.get("key", {}) or {}
	if key.get("fromMe"):
		return
	message_id = key.get("id")
	if not message_id:
		return
	if not claim_event("incoming-evolution", f"{instance_name}:{message_id}"):
		return
	if frappe.db.exists("WhatsApp Message", {"message_id": message_id, "channel": "Evolution", "send_from_instance": instance_name, "type": "Incoming"}):
		return

	remote_jid = key.get("remoteJid", "") or ""
	sender = remote_jid.split("@")[0] if "@" in remote_jid else remote_jid
	profile_name = item.get("pushName")
	msg = item.get("message", {}) or {}
	text, content_type, interactive = _extract_evolution_content(msg)
	if text is None and content_type is None:
		return

	doc = {
		"doctype": "WhatsApp Message",
		"type": "Incoming",
		"status": "Received",
		"from": sender,
		"message_id": message_id,
		"message": text or "",
		"content_type": content_type or "text",
		"profile_name": profile_name,
		"channel": "Evolution",
		"send_from_instance": instance_name,
	}
	if interactive:
		doc["interactive_payload"] = json.dumps(interactive, ensure_ascii=False)

	frappe.get_doc(doc).insert(ignore_permissions=True)


def _extract_evolution_content(msg):
	"""Return (message_value, content_type, interactive_dict|None)."""
	if msg.get("conversation"):
		return msg["conversation"], "text", None
	if msg.get("extendedTextMessage"):
		return msg["extendedTextMessage"].get("text", ""), "text", None

	btn = msg.get("buttonsResponseMessage") or msg.get("templateButtonReplyMessage")
	if btn:
		selected_id = btn.get("selectedButtonId") or btn.get("selectedId") or ""
		display = btn.get("selectedDisplayText") or btn.get("selectedButtonText") or ""
		return selected_id, "button", {"id": selected_id, "text": display}

	lst = msg.get("listResponseMessage")
	if lst:
		row = lst.get("singleSelectReply", {}) or {}
		selected_id = row.get("selectedRowId", "")
		display = lst.get("title", "")
		return selected_id, "flow", {"id": selected_id, "text": display}

	for mtype, ctype in (
		("imageMessage", "image"),
		("documentMessage", "document"),
		("videoMessage", "video"),
		("audioMessage", "audio"),
	):
		if msg.get(mtype):
			caption = msg[mtype].get("caption", "")
			return caption, ctype, None

	return None, None, None

