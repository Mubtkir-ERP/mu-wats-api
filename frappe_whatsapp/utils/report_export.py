"""Server-side report execution shared by all apps, without trusting client rows."""

import json

import frappe
from frappe import _

MAX_ROWS = 10000


def parse_object(value, label):
    if isinstance(value, str):
        try:
            value = json.loads(value or "{}")
        except ValueError:
            frappe.throw(_("Invalid {0} JSON.").format(label))
    if value is None:
        return {}
    if not isinstance(value, dict):
        frappe.throw(_("{0} must be a JSON object.").format(label))
    return value


def check_source(doctype, report=None):
    for permission in ("read", "report", "export"):
        frappe.has_permission(doctype, permission, throw=True)
    if report and (report.disabled or not report.is_permitted()):
        frappe.throw(_("Not permitted to export this report."), frappe.PermissionError)


def execute_report(report_name, filters, builder_spec=None):
    filters = parse_object(filters, "Report filters")
    spec = parse_object(builder_spec, "Report Builder settings")
    report = frappe.get_doc("Report", report_name) if report_name else None
    if spec:
        source = spec.get("args")
        if not isinstance(source, dict):
            frappe.throw(_("Report Builder arguments must be a JSON object."))
        doctype = source.get("doctype")
        if not doctype or (report and report.ref_doctype != doctype):
            frappe.throw(_("The report and Report Builder document type do not match."))
        if report and report.report_type != "Report Builder":
            frappe.throw(_("Report Builder settings require a Report Builder report."))
        check_source(doctype, report)
        return execute_builder(spec)
    if not report:
        frappe.throw(_("No report specified."))
    check_source(report.ref_doctype, report)
    if report.report_type == "Report Builder":
        columns, rows = report.get_data(filters=filters, limit=MAX_ROWS + 1, user=frappe.session.user)
    else:
        from frappe.desk.query_report import run

        result = run(report_name, filters=filters) or {}
        # Do not claim success with a blank file while Frappe is preparing data.
        if result.get("prepared_report") and result.get("result") is None:
            frappe.throw(_("This report is being prepared. Generate it first, then send it."))
        columns, rows = result.get("columns") or [], result.get("result") or []
    check_row_limit(rows)
    return columns, rows


def check_row_limit(rows):
    if len(rows) > MAX_ROWS:
        frappe.throw(_("Report exceeds {0} rows. Narrow the filters before sending.").format(MAX_ROWS))


def execute_builder(spec):
    from frappe.desk.reportview import execute, validate_args
    from frappe.model.utils import is_virtual_doctype

    source = spec.get("args")
    columns = spec.get("columns")
    if not isinstance(source, dict) or not isinstance(columns, list) or not columns:
        frappe.throw(_("Report Builder columns are missing. Refresh the report and try again."))
    # Do not forward caller-supplied permission switches, user identity, SQL,
    # pagination, or arbitrary DatabaseQuery options to Frappe.
    allowed = (
        "doctype", "fields", "filters", "or_filters", "order_by", "group_by",
        "aggregate_function", "aggregate_on_field", "aggregate_on_doctype",
    )
    args = frappe._dict({key: source[key] for key in allowed if key in source})
    args.update(start=0, page_length=MAX_ROWS + 1, save_user_settings=False, view="Report")
    validate_args(args)
    # Frappe validates field/filter permissions and applies user row permissions.
    if is_virtual_doctype(args.doctype):
        from frappe.model.base_document import get_controller
        rows = get_controller(args.doctype).get_list(args) or []
    else:
        rows = execute(**args) or []
    check_row_limit(rows)
    keys = set().union(*(row.keys() for row in rows)) if rows else None
    selected = []
    seen = set()
    for column in columns:
        if not isinstance(column, dict):
            continue
        name = column.get("fieldname")
        if not isinstance(name, str) or name in seen or (keys is not None and name not in keys):
            continue
        seen.add(name)
        selected.append({"fieldname": name, "label": str(column.get("label") or name)})
    if not selected:
        frappe.throw(_("No permitted report columns are available."))
    return selected, rows
