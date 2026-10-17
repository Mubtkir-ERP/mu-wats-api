# Copyright (c) 2026, MUBTKIR and contributors
# For license information, please see license.txt
"""WhatsApp Contact Puller.

Pull numbers from a WhatsApp instance (Evolution chats or contacts), filter by
engagement using the local WhatsApp Message history, then save the selected
numbers to a Recipient List, ERPNext Leads, or export to Excel.

Nothing here touches the Meta send/receive path.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime, add_to_date, get_datetime

from frappe_whatsapp.api import _request, _server_credentials

# Above this many selected numbers, saving is pushed to a background job.
BACKGROUND_THRESHOLD = 500


class WhatsAppContactPuller(Document):
	pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

import re as _re

def _normalize_text(t):
	"""Normalise text for keyword matching (Arabic-aware).

	Lowercases, strips Arabic diacritics/tatweel, unifies alef/ya/ta-marbuta,
	and collapses whitespace so a keyword matches despite formatting.
	"""
	if not t:
		return ""
	t = str(t).lower()
	# remove Arabic diacritics (harakat) and tatweel
	t = _re.sub(r"[\u0617-\u061A\u064B-\u0652\u0640]", "", t)
	# unify common Arabic letter variants
	t = t.replace("\u0623", "\u0627").replace("\u0625", "\u0627").replace("\u0622", "\u0627")  # hamza-alef -> alef
	t = t.replace("\u0649", "\u064a")  # alef maqsura -> ya
	t = t.replace("\u0629", "\u0647")  # ta marbuta -> ha
	# collapse whitespace
	t = _re.sub(r"\s+", " ", t).strip()
	return t


def _clean_number(jid_or_number):
	"""Reduce an Evolution JID or raw number to bare digits."""
	value = (jid_or_number or "").split("@")[0]
	return "".join(ch for ch in value if ch.isdigit())


def _resolve_instance(instance_name):
	"""Return (base_url, api_key, instance_name) for a Whatsapp Instance."""
	if not instance_name:
		frappe.throw(_("Select a Source Instance first."))
	server = frappe.db.get_value("Whatsapp Instance", instance_name, "evolution_server")
	base_url, api_key = _server_credentials(server)
	return base_url, api_key, instance_name


def _fetch_from_evolution(base_url, api_key, instance_name, source_type):
	"""Return a list of {"number","name"} from Evolution chats or contacts."""
	if source_type == "Contacts":
		path = f"/chat/findContacts/{instance_name}"
	else:
		path = f"/chat/findChats/{instance_name}"

	response = _request("POST", base_url, path, api_key, payload={})

	rows = response if isinstance(response, list) else response.get("data", response) if isinstance(response, dict) else []
	if not isinstance(rows, list):
		rows = []

	out = []
	seen = set()
	for r in rows:
		if not isinstance(r, dict):
			continue
		raw = r.get("remoteJid") or r.get("id") or r.get("number") or r.get("jid") or ""
		# Skip groups and broadcasts.
		if "@g.us" in raw or "@broadcast" in raw or "status@" in raw:
			continue
		number = _clean_number(raw)
		if not number or number in seen:
			continue
		seen.add(number)
		name = r.get("pushName") or r.get("name") or r.get("notify") or r.get("verifiedName") or ""
		out.append({"number": number, "name": name})
	return out


def _dig_records(resp):
	"""Find the message list inside any Evolution findMessages response shape.

	Different Evolution versions nest differently:
	  resp -> list
	  resp.messages.records -> list
	  resp.data -> list
	  resp.data.messages.records -> list
	This walks common containers and returns the first list of dicts found.
	"""
	def first_list(node, depth=0):
		if depth > 6:
			return None
		if isinstance(node, list):
			return node
		if isinstance(node, dict):
			# Prefer known keys first.
			for k in ("records", "messages", "data", "result", "rows"):
				if k in node:
					found = first_list(node[k], depth + 1)
					if found is not None:
						return found
			# Otherwise scan any nested value.
			for v in node.values():
				found = first_list(v, depth + 1)
				if found is not None:
					return found
		return None
	return first_list(resp) or []


def _extract_msg_text(msg):
	"""Pull readable text out of any Evolution/Baileys message object."""
	if not isinstance(msg, dict):
		return ""
	# Unwrap common wrappers.
	for wrap in ("ephemeralMessage", "viewOnceMessage", "viewOnceMessageV2", "documentWithCaptionMessage"):
		if wrap in msg and isinstance(msg[wrap], dict):
			inner = msg[wrap].get("message")
			if isinstance(inner, dict):
				msg = inner
				break
	return (
		msg.get("conversation")
		or (msg.get("extendedTextMessage") or {}).get("text")
		or (msg.get("imageMessage") or {}).get("caption")
		or (msg.get("videoMessage") or {}).get("caption")
		or (msg.get("documentMessage") or {}).get("caption")
		or (msg.get("buttonsResponseMessage") or {}).get("selectedDisplayText")
		or ((msg.get("listResponseMessage") or {}).get("title"))
		or ""
	)


def _fetch_messages_for_number(base_url, api_key, instance_name, number, limit=200):
	"""Deep history: fetch a number's messages from Evolution (/chat/findMessages).

	Returns a list of dicts: {"inbound": bool, "text": str, "ts": int|None}.
	Covers conversations that happened BEFORE the webhook was ever set up —
	the local WhatsApp Message table can't see those.
	"""
	jid = f"{number}@s.whatsapp.net"
	# Try a couple of payload shapes Evolution versions accept.
	payloads = [
		{"where": {"key": {"remoteJid": jid}}, "limit": limit},
		{"where": {"remoteJid": jid}, "limit": limit},
	]
	records = []
	for payload in payloads:
		try:
			resp = _request("POST", base_url, f"/chat/findMessages/{instance_name}", api_key, payload)
		except Exception:
			continue
		records = _dig_records(resp)
		if records:
			break

	out = []
	for m in records:
		if not isinstance(m, dict):
			continue
		key = m.get("key", {}) or {}
		inbound = not key.get("fromMe", False)
		text = _extract_msg_text(m.get("message", {}) or {})
		ts = m.get("messageTimestamp")
		if isinstance(ts, dict):
			ts = ts.get("low") or ts.get("$numberLong")
		try:
			ts = int(ts) if ts else None
		except (ValueError, TypeError):
			ts = None
		out.append({"inbound": inbound, "text": text or "", "ts": ts})
	return out


def _engagement_from_history(messages):
	"""Compute engagement signals from a deep-history message list.

	Returns {"last": datetime|None, "count": inbound_count, "hours": {hour:count},
	         "texts": [inbound texts], "last_dir": "in"|"out"|None}.
	"""
	from datetime import datetime

	count = 0
	last = None
	hours = {}
	texts = []
	last_ts = None
	last_dir = None
	for m in messages:
		ts = m.get("ts")
		dt = datetime.fromtimestamp(ts) if ts else None
		if m["inbound"]:
			count += 1
			texts.append(m["text"])
			if dt:
				hours[dt.hour] = hours.get(dt.hour, 0) + 1
				if not last or dt > last:
					last = dt
		# track most recent message direction regardless of inbound/outbound
		if ts and (last_ts is None or ts > last_ts):
			last_ts = ts
			last_dir = "in" if m["inbound"] else "out"
	return {"last": last, "count": count, "hours": hours, "texts": texts, "last_dir": last_dir}


def _engagement_map(numbers):
	"""Return {number: {"last": datetime|None, "count": int}} from local history.

	Counts inbound WhatsApp Messages per sender. The stored `from` value is
	normalised in Python (strip any @suffix and non-digits) so matching works
	whether inbound was saved clean (966...) or as a JID (966...@s.whatsapp.net).
	"""
	if not numbers:
		return {}
	wanted = set(numbers)
	rows = frappe.get_all(
		"WhatsApp Message",
		filters={"type": "Incoming"},
		fields=["`from` as number", "creation"],
	)
	result = {}
	for row in rows:
		num = _clean_number(row.number)
		if num not in wanted:
			continue
		entry = result.setdefault(num, {"last": None, "count": 0})
		entry["count"] += 1
		if not entry["last"] or get_datetime(row.creation) > get_datetime(entry["last"]):
			entry["last"] = row.creation
	return result


def _keyword_matches(numbers, keyword):
	"""Return the subset of numbers whose inbound messages contain keyword."""
	if not keyword:
		return set(numbers)
	wanted = set(numbers)
	rows = frappe.get_all(
		"WhatsApp Message",
		filters={"type": "Incoming", "message": ["like", f"%{keyword}%"]},
		fields=["`from` as number"],
	)
	return {_clean_number(r.number) for r in rows if _clean_number(r.number) in wanted}


def _status_of(last, ref_now, active_days=30, idle_days=90):
	"""Classify engagement by last-interaction age (configurable windows)."""
	if not last:
		return "Dead"
	age_days = (ref_now - get_datetime(last)).days
	if age_days <= active_days:
		return "Active"
	if age_days <= idle_days:
		return "Idle"
	return "Dead"


def _tier_of(status, count, a_min_msgs=5):
	"""Auto A/B/C tier from engagement (A threshold configurable).

	A = Active and chatty (>= a_min_msgs inbound), B = Active/Idle with >=1
	inbound, C = dormant or no engagement.
	"""
	if status == "Active" and count >= a_min_msgs:
		return "A"
	if status in ("Active", "Idle") and count >= 1:
		return "B"
	return "C"


def _best_hour_map(numbers):
	"""Idea 8: most frequent reply hour per number, from inbound history."""
	if not numbers:
		return {}
	wanted = set(numbers)
	rows = frappe.get_all(
		"WhatsApp Message",
		filters={"type": "Incoming"},
		fields=["`from` as number", "creation"],
	)
	buckets = {}
	for row in rows:
		num = _clean_number(row.number)
		if num not in wanted or not row.creation:
			continue
		hour = get_datetime(row.creation).hour
		buckets.setdefault(num, {}).setdefault(hour, 0)
		buckets[num][hour] += 1
	result = {}
	for num, hours in buckets.items():
		best = max(hours, key=hours.get)
		result[num] = f"{best:02d}:00"
	return result


def _read_no_reply_set(numbers):
	"""Idea 9: numbers whose latest message is an outbound (no inbound after).

	Approximation from local history: if the most recent message with this
	number is Outgoing, they haven't replied since.
	"""
	if not numbers:
		return set()
	wanted = set(numbers)
	rows = frappe.get_all(
		"WhatsApp Message",
		filters={},
		fields=["`from` as frm", "`to` as t", "type", "creation"],
		order_by="creation desc",
	)
	latest_dir = {}
	for row in rows:
		num = _clean_number(row.frm if row.type == "Incoming" else row.t)
		if num not in wanted or num in latest_dir:
			continue
		latest_dir[num] = row.type
	return {n for n, d in latest_dir.items() if d == "Outgoing"}


# ---------------------------------------------------------------------------
# Whitelisted actions (called from the client script)
# ---------------------------------------------------------------------------

@frappe.whitelist()
def pull(docname):
	"""Fetch + filter numbers and write them into the doc's child table."""
	frappe.has_permission("WhatsApp Contact Puller", "write", docname, throw=True)
	doc = frappe.get_doc("WhatsApp Contact Puller", docname)

	base_url, api_key, instance_name = _resolve_instance(doc.source_instance)
	raw = _fetch_from_evolution(base_url, api_key, instance_name, doc.source_type or "Chats")

	numbers = [r["number"] for r in raw]
	ref_now = now_datetime()

	# Configurable thresholds (fall back to sensible defaults).
	active_days = int(doc.active_within_days or 30)
	idle_days = int(doc.idle_within_days or 90)
	a_min = int(doc.tier_a_min_msgs or 5)
	deep = bool(doc.deep_history)
	hist_limit = int(doc.history_limit or 200)
	keyword = _normalize_text(doc.keyword_filter or "")

	# Local (ERPNext) engagement as fallback when deep history is off.
	local_eng = {} if deep else _engagement_map(numbers)
	local_hours = {} if deep else _best_hour_map(numbers)
	local_rnr = set() if deep else _read_no_reply_set(numbers)
	local_kw = None if deep else _keyword_matches(numbers, keyword)

	effective_status = doc.engagement_status
	if doc.winback_preset:
		effective_status = "Dead"

	cutoff = None
	if doc.replied_within_days and int(doc.replied_within_days) > 0:
		cutoff = add_to_date(ref_now, days=-int(doc.replied_within_days))

	doc.set("pulled_numbers", [])
	kept = 0
	tier_counts = {"A": 0, "B": 0, "C": 0}

	for r in raw:
		number, name = r["number"], r["name"]

		if doc.only_with_name and not name:
			continue

		# Gather engagement signals: deep (Evolution history) or local (ERPNext).
		if deep:
			msgs = _fetch_messages_for_number(base_url, api_key, instance_name, number, hist_limit)
			h = _engagement_from_history(msgs)
			last, count, hours = h["last"], h["count"], h["hours"]
			texts = h["texts"]
			read_no_reply = 1 if h["last_dir"] == "out" else 0
		else:
			eng = local_eng.get(number, {"last": None, "count": 0})
			last, count = eng["last"], eng["count"]
			hours = None
			texts = None
			read_no_reply = 1 if number in local_rnr else 0

		# Keyword filter (searches deep texts, or local match set).
		if keyword:
			if deep:
				if not any(keyword in _normalize_text(t) for t in texts):
					continue
			else:
				if number not in (local_kw or set()):
					continue

		if cutoff and (not last or get_datetime(last) < get_datetime(cutoff)):
			continue
		if doc.min_inbound_count and count < int(doc.min_inbound_count):
			continue

		status = _status_of(last, ref_now, active_days, idle_days)
		if effective_status and effective_status != "All":
			if status != effective_status:
				continue

		if doc.verify_whatsapp:
			if not _verify_number(base_url, api_key, instance_name, number):
				continue

		# Best hour: from deep hours dict, or local map.
		if deep:
			best_hour = f"{max(hours, key=hours.get):02d}:00" if hours else ""
		else:
			best_hour = local_hours.get(number, "")

		tier = _tier_of(status, count, a_min)
		tier_counts[tier] += 1
		exists_in = _where_exists(number)
		doc.append("pulled_numbers", {
			"selected": 1,
			"mobile_number": number,
			"contact_name": name,
			"last_interaction": last,
			"inbound_count": count,
			"tier": tier,
			"best_send_hour": best_hour,
			"read_no_reply": read_no_reply,
			"already_exists": 1 if exists_in else 0,
			"exists_in": exists_in or "",
		})
		kept += 1

	doc.pulled_count = kept
	doc.selected_count = kept
	engaged = tier_counts["A"] + tier_counts["B"]
	doc.quality_score = round((engaged / kept) * 100, 1) if kept else 0
	doc.tier_summary = f"A: {tier_counts['A']}  |  B: {tier_counts['B']}  |  C: {tier_counts['C']}"
	doc.save()
	return {"pulled": kept, "quality": doc.quality_score, "tiers": tier_counts}


def _verify_number(base_url, api_key, instance_name, number):
	"""Check a single number has WhatsApp via Evolution."""
	try:
		resp = _request(
			"POST", base_url, f"/chat/whatsappNumbers/{instance_name}",
			api_key, payload={"numbers": [number]},
		)
		items = resp if isinstance(resp, list) else resp.get("data", [])
		for it in items or []:
			if isinstance(it, dict) and it.get("exists"):
				return True
		return False
	except Exception:
		# On verification error, keep the number rather than dropping silently.
		return True


def _where_exists(number):
	"""Return a short label if the number already exists as Lead/Customer."""
	labels = []
	if frappe.db.exists("Lead", {"mobile_no": number}) or frappe.db.exists("Lead", {"phone": number}):
		labels.append("Lead")
	if frappe.db.exists("Contact Phone", {"phone": number}):
		labels.append("Contact")
	return ", ".join(labels)


@frappe.whitelist()
def toggle_select_all(docname, value):
	"""Set the `selected` flag on every row."""
	frappe.has_permission("WhatsApp Contact Puller", "write", docname, throw=True)
	value = 1 if str(value) in ("1", "true", "True") else 0
	doc = frappe.get_doc("WhatsApp Contact Puller", docname)
	for row in doc.pulled_numbers:
		row.selected = value
	doc.selected_count = sum(1 for r in doc.pulled_numbers if r.selected)
	doc.save()
	return {"selected": doc.selected_count}


@frappe.whitelist()
def save_selected(docname):
	"""Save selected numbers to the chosen destination.

	Routes to a background job above BACKGROUND_THRESHOLD selected rows.
	"""
	frappe.has_permission("WhatsApp Contact Puller", "write", docname, throw=True)
	doc = frappe.get_doc("WhatsApp Contact Puller", docname)

	selected = [r for r in doc.pulled_numbers if r.selected]
	if not selected:
		frappe.throw(_("No numbers selected."))

	if len(selected) > BACKGROUND_THRESHOLD:
		frappe.enqueue(
			"frappe_whatsapp.frappe_whatsapp.doctype.whatsapp_contact_puller.whatsapp_contact_puller._do_save",
			queue="long", timeout=1500, docname=docname,
		)
		return {"queued": True, "count": len(selected)}

	return _do_save(docname)


def _do_save(docname):
	"""Perform the actual save (runs inline or in a background job)."""
	doc = frappe.get_doc("WhatsApp Contact Puller", docname)
	selected = [r for r in doc.pulled_numbers if r.selected]

	if doc.destination == "Recipient List":
		result = _save_to_recipient_list(doc, selected)
	elif doc.destination == "Lead":
		result = _save_to_leads(doc, selected)
	else:
		frappe.throw(_("Use the Export to Excel button for Excel output."))
		return

	frappe.publish_realtime(
		"whatsapp_puller_saved", {"docname": docname, **result},
		user=frappe.session.user,
	)
	return result


def _save_to_recipient_list(doc, selected):
	"""Append selected numbers to a new or existing Recipient List (deduped)."""
	if doc.target_recipient_list:
		rl = frappe.get_doc("WhatsApp Recipient List", doc.target_recipient_list)
	else:
		name = doc.new_list_name or f"Pull {doc.name}"
		rl = frappe.get_doc({
			"doctype": "WhatsApp Recipient List",
			"list_name": name,
			"description": f"Imported from {doc.name} ({doc.source_instance})",
		})

	existing = {r.mobile_number for r in rl.get("recipients", [])}
	# Continue the recip-num-### sequence from what the list already has.
	seq = len(rl.get("recipients", []))
	added = 0
	for row in selected:
		if row.mobile_number in existing:
			continue
		seq += 1
		rl.append("recipients", {
			"recipient_code": f"recip-num-{seq:03d}",
			"mobile_number": row.mobile_number,
			"recipient_name": row.contact_name or "",
			"recipient_data": frappe.as_json({
				"name": row.contact_name or "",
				"tier": row.get("tier") or "",
				"best_send_hour": row.get("best_send_hour") or "",
			}),
		})
		existing.add(row.mobile_number)
		added += 1

	rl.save(ignore_permissions=True)
	return {"destination": "Recipient List", "list": rl.name, "added": added}


def _save_to_leads(doc, selected):
	"""Create standard ERPNext Leads for numbers not already a Lead (deduped)."""
	created, skipped = 0, 0
	source = doc.lead_source or "WhatsApp Pull"
	# Company now lives on the Recipient List, not the puller; use the target
	# list's company when one is selected.
	company = None
	if doc.target_recipient_list:
		company = frappe.db.get_value("WhatsApp Recipient List", doc.target_recipient_list, "company")
	for row in selected:
		number = row.mobile_number
		if frappe.db.exists("Lead", {"mobile_no": number}) or frappe.db.exists("Lead", {"phone": number}):
			skipped += 1
			continue
		lead = frappe.get_doc({
			"doctype": "Lead",
			"lead_name": row.contact_name or number,
			"mobile_no": number,
			"phone": number,
			"company": company or None,
			"source": source if frappe.db.exists("Lead Source", source) else None,
		})
		lead.insert(ignore_permissions=True)
		created += 1
	return {"destination": "Lead", "created": created, "skipped": skipped}


@frappe.whitelist()
def export_excel(docname):
	"""Return the selected numbers as an .xlsx download."""
	frappe.has_permission("WhatsApp Contact Puller", "read", docname, throw=True)
	doc = frappe.get_doc("WhatsApp Contact Puller", docname)
	selected = [r for r in doc.pulled_numbers if r.selected]
	if not selected:
		frappe.throw(_("No numbers selected."))

	from frappe.utils.xlsxutils import make_xlsx

	data = [["Mobile Number", "Name", "Tier", "Best Hour", "Last Interaction",
	         "Inbound Msgs", "Read/No Reply", "Already Exists"]]
	for row in selected:
		data.append([
			row.mobile_number,
			row.contact_name or "",
			row.get("tier") or "",
			row.get("best_send_hour") or "",
			str(row.last_interaction or ""),
			row.inbound_count or 0,
			"Yes" if row.get("read_no_reply") else "No",
			"Yes" if row.already_exists else "No",
		])

	xlsx_file = make_xlsx(data, "WhatsApp Numbers")
	frappe.response["filename"] = f"{doc.name}.xlsx"
	frappe.response["filecontent"] = xlsx_file.getvalue()
	frappe.response["type"] = "binary"
