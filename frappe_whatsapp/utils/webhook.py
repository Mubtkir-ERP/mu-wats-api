"""Webhook."""
import frappe
import json
import requests
import time
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
	"""Update message status."""
	id = data['statuses'][0]['id']
	status = data['statuses'][0]['status']
	conversation = data['statuses'][0].get('conversation', {}).get('id')
	name = frappe.db.get_value("WhatsApp Message", filters={"message_id": id})

	doc = frappe.get_doc("WhatsApp Message", name)
	doc.status = status
	if conversation:
		doc.conversation_id = conversation
	doc.save(ignore_permissions=True)

# ---------------------------------------------------------------------------
# Evolution (Mubtkir API) inbound webhook — separate endpoint, does not touch
# the Meta webhook() above. Configure the Evolution instance to POST events to:
#   /api/method/frappe_whatsapp.utils.webhook.evolution_webhook
# ---------------------------------------------------------------------------

@frappe.whitelist(allow_guest=True)
def evolution_webhook():
	"""Receive Evolution API events and store incoming messages.

	Handles the messages.upsert event for text, media, buttons and list
	replies. Each inbound message becomes a WhatsApp Message (type=Incoming)
	so downstream doc_events hooks (e.g. the challenge engine) can react.
	Outbound echoes (fromMe) and duplicates (by message id) are ignored.
	"""
	if frappe.request.method == "GET":
		return Response("OK", status=200)

	data = frappe.local.form_dict

	# Log the raw event for debugging/audit.
	frappe.get_doc({
		"doctype": "WhatsApp Notification Log",
		"template": "Webhook",
		"meta_data": json.dumps(data),
	}).insert(ignore_permissions=True)

	event = data.get("event") or ""
	if event and event.replace(".", "_").lower() != "messages_upsert":
		# Only messages.upsert carries inbound messages; ignore others quietly.
		return Response("OK", status=200)

	payload = data.get("data") or {}
	# Evolution may send a single object or a list.
	items = payload if isinstance(payload, list) else [payload]

	for item in items:
		_process_evolution_message(item)

	return Response("OK", status=200)


def _process_evolution_message(item):
	"""Parse one Evolution message object and save it as Incoming."""
	if not isinstance(item, dict):
		return

	key = item.get("key", {}) or {}
	# Skip our own outgoing echoes.
	if key.get("fromMe"):
		return

	message_id = key.get("id")
	if not message_id:
		return

	# Deduplicate: if we already stored this id, stop.
	if frappe.db.exists("WhatsApp Message", {"message_id": message_id}):
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
		"from": sender,
		"message_id": message_id,
		"message": text or "",
		"content_type": content_type or "text",
		"profile_name": profile_name,
		"channel": "Evolution",
	}
	if interactive:
		# Store the visible option text alongside the id (which is in message).
		doc["interactive_payload"] = json.dumps(interactive)

	frappe.get_doc(doc).insert(ignore_permissions=True)


def _extract_evolution_content(msg):
	"""Return (message_value, content_type, interactive_dict|None).

	For button/list replies the selected option id goes into message_value and
	the visible title is returned in the interactive dict as {"id","text"}.
	"""
	# Plain text
	if msg.get("conversation"):
		return msg["conversation"], "text", None
	if msg.get("extendedTextMessage"):
		return msg["extendedTextMessage"].get("text", ""), "text", None

	# Button reply (templateButtonReply / buttonsResponseMessage)
	btn = msg.get("buttonsResponseMessage") or msg.get("templateButtonReplyMessage")
	if btn:
		selected_id = btn.get("selectedButtonId") or btn.get("selectedId") or ""
		display = btn.get("selectedDisplayText") or btn.get("selectedButtonText") or ""
		return selected_id, "button", {"id": selected_id, "text": display}

	# List reply
	lst = msg.get("listResponseMessage")
	if lst:
		row = lst.get("singleSelectReply", {}) or {}
		selected_id = row.get("selectedRowId", "")
		display = lst.get("title", "")
		return selected_id, "flow", {"id": selected_id, "text": display}

	# Media types
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
