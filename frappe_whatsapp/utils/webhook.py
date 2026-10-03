"""Webhook."""
import frappe
import json
import hmac
import requests
from werkzeug.wrappers import Response
import frappe.utils


@frappe.whitelist(allow_guest=True)
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

	if frappe.form_dict.get("hub.verify_token") != webhook_verify_token:
		frappe.throw("Verify token does not match")

	return Response(hub_challenge, status=200)

def post():
	"""Post."""
	data = frappe.local.form_dict
	frappe.get_doc({
		"doctype": "WhatsApp Notification Log",
		"template": "Webhook",
		"meta_data": json.dumps(data)
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
			message_type = message['type']
			is_reply = True if message.get('context') and 'forwarded' not in message.get('context') else False
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
					"message": message['interactive']['nfm_reply']['response_json'],
					"message_id": message['id'],
					"reply_to_message_id": reply_to_message_id,
					"is_reply": is_reply,
					"content_type": "flow",
					"profile_name":sender_profile_name
				}).insert(ignore_permissions=True)
			elif message_type in ["image", "audio", "video", "document"]:
				settings = frappe.get_doc(
							"WhatsApp Settings", "WhatsApp Settings",
						)
				token = settings.get_password("token")
				url = f"{settings.url}/{settings.version}/"


				media_id = message[message_type]["id"]
				headers = {
					'Authorization': 'Bearer ' + token

				}
				response = requests.get(f'{url}{media_id}/', headers=headers)

				if response.status_code == 200:
					media_data = response.json()
					media_url = media_data.get("url")
					mime_type = media_data.get("mime_type")
					file_extension = mime_type.split('/')[1]

					media_response = requests.get(media_url, headers=headers)
					if media_response.status_code == 200:

						file_data = media_response.content
						file_name = f"{frappe.generate_hash(length=10)}.{file_extension}"

						message_doc = frappe.get_doc({
							"doctype": "WhatsApp Message",
							"type": "Incoming",
							"from": message['from'],
							"message_id": message['id'],
							"reply_to_message_id": reply_to_message_id,
							"is_reply": is_reply,
							"message": message[message_type].get("caption",f"/files/{file_name}"),
							"content_type" : message_type,
							"profile_name":sender_profile_name
						}).insert(ignore_permissions=True)

						file = frappe.get_doc(
							{
								"doctype": "File",
								"file_name": file_name,
								"attached_to_doctype": "WhatsApp Message",
								"attached_to_name": message_doc.name,
								"content": file_data,
								"attached_to_field": "attach"
							}
						).save(ignore_permissions=True)


						message_doc.attach = file.file_url
						message_doc.save()
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
					"message": message[message_type].get(message_type),
					"content_type" : message_type,
					"profile_name":sender_profile_name
				}).insert(ignore_permissions=True)

	else:
		changes = None
		try:
			changes = data["entry"][0]["changes"][0]
		except KeyError:
			changes = data["entry"]["changes"][0]
		update_status(changes)
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
	"""Update a Meta outbound message status without storing lowercase values."""
	from frappe_whatsapp.utils.messaging import can_advance_status, normalise_message_status

	statuses = data.get("statuses") or []
	if not statuses:
		return
	item = statuses[0]
	message_id = item.get("id")
	status = normalise_message_status(item.get("status"))
	conversation = item.get("conversation", {}).get("id")
	if not message_id or not status:
		return

	name = frappe.db.get_value("WhatsApp Message", {"message_id": message_id}, "name")
	if not name:
		return
	current = frappe.db.get_value("WhatsApp Message", name, "status")
	if not can_advance_status(current, status):
		return
	values = {"status": status}
	if conversation:
		values["conversation_id"] = conversation
	frappe.db.set_value("WhatsApp Message", name, values)

# ---------------------------------------------------------------------------
# Evolution (Mubtkir API) inbound webhook
# ---------------------------------------------------------------------------


def _request_header(name):
	try:
		return frappe.get_request_header(name) or ""
	except Exception:
		return (getattr(frappe.local, "request", None).headers.get(name, "") if getattr(frappe.local, "request", None) else "")


def _configured_evolution_secret():
	settings = frappe.get_doc("WhatsApp Settings", "WhatsApp Settings")
	try:
		return settings.get_password("evolution_webhook_secret", raise_exception=False) or ""
	except TypeError:
		try:
			return settings.get_password("evolution_webhook_secret") or ""
		except Exception:
			return ""
	except Exception:
		return ""


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
	"""Authenticate the webhook when a shared secret is configured.

	The endpoint accepts the secret through an HTTP header, Bearer token or query
	parameter so it works with different Evolution deployments. Even without a
	secret, an event must identify an instance that exists on this ERPNext site;
	unknown-instance payloads are rejected instead of being stored blindly.
	"""
	secret = _configured_evolution_secret()
	if secret:
		authorization = _request_header("Authorization")
		bearer = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
		provided = (
			_request_header("X-Webhook-Secret")
			or _request_header("X-Evolution-Webhook-Secret")
			or bearer
			or str(frappe.form_dict.get("secret") or "")
		)
		if not provided or not hmac.compare_digest(str(secret), str(provided)):
			frappe.throw("Invalid Evolution webhook secret", frappe.PermissionError)

	instance_name = _evolution_instance_name(data)
	if not instance_name or not frappe.db.exists("Whatsapp Instance", instance_name):
		frappe.throw("Unknown Evolution webhook instance", frappe.PermissionError)
	return instance_name


@frappe.whitelist(allow_guest=True)
def evolution_webhook():
	"""Receive authenticated Evolution message/status events."""
	if frappe.request.method == "GET":
		return Response("OK", status=200)
	if frappe.request.method != "POST":
		return Response("Method Not Allowed", status=405)

	data = frappe.local.form_dict
	instance_name = _validate_evolution_webhook(data)

	# Only authenticated / known-instance payloads are persisted to the audit log.
	audit_data = dict(data)
	audit_data.pop("secret", None)
	frappe.get_doc({
		"doctype": "WhatsApp Notification Log",
		"template": "Evolution Webhook",
		"meta_data": json.dumps(audit_data, default=str),
	}).insert(ignore_permissions=True)

	event = (data.get("event") or "").replace(".", "_").lower()
	payload = data.get("data") or {}
	items = payload if isinstance(payload, list) else [payload]

	if event == "messages_update":
		for item in items:
			_process_evolution_status(item)
		return Response("OK", status=200)

	if event and event != "messages_upsert":
		return Response("OK", status=200)

	for item in items:
		_process_evolution_message(item, instance_name=instance_name)

	return Response("OK", status=200)


def _process_evolution_status(item):
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

	name = frappe.db.get_value("WhatsApp Message", {"message_id": message_id}, "name")
	if not name:
		return
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
	if not message_id or frappe.db.exists("WhatsApp Message", {"message_id": message_id}):
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

