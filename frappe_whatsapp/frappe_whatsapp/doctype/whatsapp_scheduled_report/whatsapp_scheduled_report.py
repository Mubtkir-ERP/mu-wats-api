# Copyright (c) 2026, Shridhar Patil and contributors
# For license information, please see license.txt
"""Automatic, periodic delivery of ERPNext reports over WhatsApp.

Each record picks a Report, an output format (PDF / Excel / CSV), a recipient
(resolved from a Party Type + Party, or an explicit mobile number) and a
schedule. An hourly scheduled job renders the report and sends it as a WhatsApp
document to the resolved number.
"""

import csv
import io
import json

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import get_url, getdate, now, now_datetime, nowdate

# Format -> (file extension, document mime type sent to WhatsApp).
_FORMAT_META = {
	"PDF": ("pdf", "application/pdf"),
	"Excel": ("xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
	"CSV": ("csv", "text/csv"),
}


class WhatsAppScheduledReport(Document):
	"""A scheduled WhatsApp delivery of one ERPNext report."""

	def validate(self):
		"""Ensure the filters field holds a valid JSON object."""
		if self.filters_json:
			try:
				parsed = json.loads(self.filters_json)
			except Exception:
				frappe.throw(_("Report Filters must be valid JSON."))
			if not isinstance(parsed, dict):
				frappe.throw(_("Report Filters must be a JSON object (e.g. {\"company\": \"My Co\"})."))

	# ---- helpers -----------------------------------------------------------
	def _filters(self):
		if not self.filters_json:
			return {}
		try:
			return json.loads(self.filters_json)
		except Exception:
			return {}

	def _run_report(self):
		"""Run the report and return its columns + result rows."""
		from frappe.desk.query_report import run as run_query_report

		res = run_query_report(self.report, filters=self._filters()) or {}
		return res.get("columns") or [], res.get("result") or []

	@staticmethod
	def _normalise(columns, result):
		"""Return ``(headers, rows)`` from Frappe's varied report shapes."""
		col_defs = []
		for c in columns:
			if isinstance(c, dict):
				col_defs.append(
					{
						"label": c.get("label") or c.get("fieldname") or "",
						"fieldname": c.get("fieldname") or c.get("label") or "",
					}
				)
			else:
				label = str(c).split(":")[0]
				col_defs.append({"label": label, "fieldname": label})

		headers = [c["label"] for c in col_defs]
		rows = []
		for r in result:
			if isinstance(r, dict):
				rows.append([r.get(c["fieldname"], r.get(c["label"], "")) for c in col_defs])
			elif isinstance(r, (list, tuple)):
				rows.append(list(r))
			else:
				rows.append([r])
		return headers, rows

	def _render_file(self):
		"""Render the report to bytes. Returns ``(filename, content, mimetype)``."""
		fmt = self.report_format or "PDF"
		ext, mimetype = _FORMAT_META.get(fmt, _FORMAT_META["PDF"])
		headers, rows = self._normalise(*self._run_report())

		safe_title = (self.report_title or self.report or "report").replace("/", "-").replace(" ", "_")
		filename = f"{safe_title}.{ext}"

		if fmt == "CSV":
			buf = io.StringIO()
			writer = csv.writer(buf)
			writer.writerow(headers)
			for r in rows:
				writer.writerow(["" if v is None else v for v in r])
			# BOM so Arabic opens correctly in Excel.
			content = ("﻿" + buf.getvalue()).encode("utf-8")

		elif fmt == "Excel":
			from frappe.utils.xlsxutils import make_xlsx

			data = [headers] + [["" if v is None else v for v in r] for r in rows]
			xlsx = make_xlsx(data, "Report")
			content = xlsx.getvalue()

		else:  # PDF
			from frappe.utils.pdf import get_pdf

			content = get_pdf(self._html_table(headers, rows))

		return filename, content, mimetype

	def _html_table(self, headers, rows):
		esc = frappe.utils.escape_html
		ths = "".join(
			f'<th style="border:1px solid #bbb;padding:5px;background:#f4f4f4;text-align:right">{esc(str(h))}</th>'
			for h in headers
		)
		trs = ""
		for r in rows:
			tds = "".join(
				f'<td style="border:1px solid #ddd;padding:5px;text-align:right">{esc("" if v is None else str(v))}</td>'
				for v in r
			)
			trs += f"<tr>{tds}</tr>"
		return (
			f'<div style="font-family:sans-serif"><h3 style="text-align:right">{esc(self.report_title or self.report or "")}</h3>'
			f'<table style="border-collapse:collapse;width:100%;font-size:11px">'
			f"<thead><tr>{ths}</tr></thead><tbody>{trs}</tbody></table></div>"
		)

	def _save_file(self, filename, content):
		"""Persist the rendered content as a File and return its URL."""
		f = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": filename,
				"content": content,
				"is_private": 0,
			}
		).insert(ignore_permissions=True)
		return f.file_url

	def _resolve_party_mobile(self):
		"""Resolve the recipient's mobile from the Party's primary contact."""
		links = frappe.get_all(
			"Dynamic Link",
			filters={
				"link_doctype": self.party_type,
				"link_name": self.party,
				"parenttype": "Contact",
			},
			fields=["parent"],
			order_by="idx asc",
			limit_page_length=1,
		)
		if links:
			mobile = frappe.db.get_value("Contact", links[0].parent, "mobile_no")
			if mobile:
				return mobile

		# Fallback: a phone-like field directly on the party document
		# (e.g. Employee.cell_number, Customer.mobile_no).
		meta = frappe.get_meta(self.party_type)
		for field in ("mobile_no", "mobile", "cell_number", "phone", "whatsapp_no", "contact_mobile"):
			if meta.has_field(field):
				val = frappe.db.get_value(self.party_type, self.party, field)
				if val:
					return val
		return None

	def _recipient_mobile(self):
		number = None
		if self.mobile_number:
			number = self.mobile_number
		elif self.party_type and self.party:
			number = self._resolve_party_mobile()
		if not number:
			frappe.throw(
				_("Could not resolve a recipient mobile number. Set a Party with a contact, or a mobile override.")
			)
		return str(number).strip().lstrip("+")

	def _resolve_instance(self):
		name = self.whatsapp_instance
		if not name:
			name = frappe.db.get_value("Whatsapp Instance", {"linked_user": frappe.session.user}, "name")
		if not name:
			name = frappe.db.get_value("Whatsapp Instance", {"connection_status": "Connected"}, "name")
		if not name:
			frappe.throw(_("No WhatsApp instance available to send from. Set 'Send From Instance'."))
		return name

	def _caption(self):
		"""Accompanying message text: from a WhatsApp Template or the free-text caption."""
		if self.use_template and self.template:
			tmpl = frappe.db.get_value("WhatsApp Templates", self.template, "*")
			if tmpl:
				return (
					tmpl.get("template")
					or tmpl.get("message_content")
					or tmpl.get("body")
					or tmpl.get("message")
					or ""
				)
		return self.caption or ""

	def _send_file(self, file_url, filename, mimetype):
		from frappe_whatsapp.api import _request, _server_credentials

		instance_name = self._resolve_instance()
		server = frappe.db.get_value("Whatsapp Instance", instance_name, "evolution_server")
		base_url, api_key = _server_credentials(server)

		payload = {
			"number": self._recipient_mobile(),
			"mediatype": "document",
			"media": get_url() + file_url,
			"fileName": filename,
			"mimetype": mimetype,
		}
		caption = self._caption()
		if caption:
			payload["caption"] = caption

		_request("POST", base_url, f"/message/sendMedia/{instance_name}", api_key, payload)

	# ---- public actions ----------------------------------------------------
	@frappe.whitelist()
	def send_now(self):
		"""Render and send the report immediately. Returns a small status dict."""
		filename, content, mimetype = self._render_file()
		file_url = self._save_file(filename, content)
		self._send_file(file_url, filename, mimetype)
		self.db_set("last_sent_on", now())
		return {"sent": True, "file": filename}

	def is_due(self, today, current_hour, current_weekday):
		"""True when this report's schedule matches the given day/hour."""
		if self.disabled:
			return False
		repeat = self.repeat_every or 1
		scheduled_hour = int(str(self.schedule_time or "08:00:00")[:2]) if self.schedule_time else 8
		if scheduled_hour != current_hour:
			return False

		if self.frequency == "Daily":
			return today.day % repeat == 0
		if self.frequency == "Weekly":
			return bool(
				self.week_day
				and current_weekday == self.week_day
				and today.isocalendar()[1] % repeat == 0
			)
		if self.frequency == "Monthly":
			return bool(self.schedule_day and today.day == self.schedule_day and today.month % repeat == 0)
		return False


def trigger_scheduled_reports():
	"""Scheduled (hourly): send every scheduled report whose time matches now."""
	if frappe.flags.in_import or frappe.flags.in_patch:
		return

	today = getdate(nowdate())
	now_dt = now_datetime()
	current_hour = now_dt.hour
	current_weekday = today.strftime("%A")

	names = frappe.get_all("WhatsApp Scheduled Report", filters={"disabled": 0}, pluck="name")
	for name in names:
		try:
			doc = frappe.get_doc("WhatsApp Scheduled Report", name)
			if doc.is_due(today, current_hour, current_weekday):
				doc.send_now()
		except Exception:
			frappe.log_error(frappe.get_traceback(), f"Scheduled report failed: {name}")
