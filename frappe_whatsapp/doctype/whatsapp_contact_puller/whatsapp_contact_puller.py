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


def _engagement_map(numbers):
	"""Return {number: {"last": datetime|None, "count": int}} from local history.

	Counts inbound WhatsApp Messages per sender. One grouped query, not N.
	"""
	if not numbers:
		return {}
	rows = frappe.get_all(
		"WhatsApp Message",
		filters={"type": "Incoming", "from": ["in", list(numbers)]},
		fields=["`from` as number", "max(creation) as last", "count(name) as cnt"],
		group_by="`from`",
	)
	result = {}
	for row in rows:
		result[row.number] = {"last": row.last, "count": row.cnt or 0}
	return result


def _keyword_matches(numbers, keyword):
	"""Return the subset of numbers whose inbound messages contain keyword."""
	if not keyword:
		return set(numbers)
	rows = frappe.get_all(
		"WhatsApp Message",
		filters={
			"type": "Incoming",
			"from": ["in", list(numbers)],
			"message": ["like", f"%{keyword}%"],
		},
		fields=["`from` as number"],
		group_by="`from`",
	)
	return {r.number for r in rows}


def _status_of(last, ref_now):
	"""Classify engagement by last-interaction age."""
	if not last:
		return "Dead"
	age_days = (ref_now - get_datetime(last)).days
	if age_days <= 30:
		return "Active"
	if age_days <= 90:
		return "Idle"
	return "Dead"


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
	engagement = _engagement_map(numbers)
	keyword_set = _keyword_matches(numbers, (doc.keyword_filter or "").strip())
	ref_now = now_datetime()

	cutoff = None
	if doc.replied_within_days and int(doc.replied_within_days) > 0:
		cutoff = add_to_date(ref_now, days=-int(doc.replied_within_days))

	doc.set("pulled_numbers", [])
	kept = 0
	for r in raw:
		number, name = r["number"], r["name"]

		if doc.only_with_name and not name:
			continue
		if (doc.keyword_filter or "").strip() and number not in keyword_set:
			continue

		eng = engagement.get(number, {"last": None, "count": 0})
		last, count = eng["last"], eng["count"]

		if cutoff and (not last or get_datetime(last) < get_datetime(cutoff)):
			continue
		if doc.min_inbound_count and count < int(doc.min_inbound_count):
			continue
		if doc.engagement_status and doc.engagement_status != "All":
			if _status_of(last, ref_now) != doc.engagement_status:
				continue

		if doc.verify_whatsapp:
			if not _verify_number(base_url, api_key, instance_name, number):
				continue

		exists_in = _where_exists(number)
		doc.append("pulled_numbers", {
			"selected": 1,
			"mobile_number": number,
			"contact_name": name,
			"last_interaction": last,
			"inbound_count": count,
			"already_exists": 1 if exists_in else 0,
			"exists_in": exists_in or "",
		})
		kept += 1

	doc.pulled_count = kept
	doc.selected_count = kept
	doc.save()
	return {"pulled": kept}


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
	added = 0
	for row in selected:
		if row.mobile_number in existing:
			continue
		rl.append("recipients", {
			"mobile_number": row.mobile_number,
			"recipient_name": row.contact_name or "",
			"recipient_data": frappe.as_json({"name": row.contact_name or ""}),
		})
		existing.add(row.mobile_number)
		added += 1

	rl.save(ignore_permissions=True)
	return {"destination": "Recipient List", "list": rl.name, "added": added}


def _save_to_leads(doc, selected):
	"""Create standard ERPNext Leads for numbers not already a Lead (deduped)."""
	created, skipped = 0, 0
	source = doc.lead_source or "WhatsApp Pull"
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

	data = [["Mobile Number", "Name", "Last Interaction", "Inbound Msgs", "Already Exists"]]
	for row in selected:
		data.append([
			row.mobile_number,
			row.contact_name or "",
			str(row.last_interaction or ""),
			row.inbound_count or 0,
			"Yes" if row.already_exists else "No",
		])

	xlsx_file = make_xlsx(data, "WhatsApp Numbers")
	frappe.response["filename"] = f"{doc.name}.xlsx"
	frappe.response["filecontent"] = xlsx_file.getvalue()
	frappe.response["type"] = "binary"
