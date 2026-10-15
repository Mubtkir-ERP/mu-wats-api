"""Run standalone: python tests/test_report_export.py (no Frappe or HTTP)."""

import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock


class Dict(dict):
    __getattr__ = dict.get
    __setattr__ = dict.__setitem__


frappe = types.ModuleType("frappe")
frappe._ = lambda text: text
frappe._dict = Dict
frappe.session = Dict(user="report-user")
frappe.PermissionError = PermissionError


def fail(text, *args):
    raise ValueError(text)


frappe.throw = fail
sys.modules["frappe"] = frappe
reportview = types.ModuleType("frappe.desk.reportview")
query_report = types.ModuleType("frappe.desk.query_report")
sys.modules[reportview.__name__] = reportview
sys.modules[query_report.__name__] = query_report
model_utils = types.ModuleType("frappe.model.utils")
model_utils.is_virtual_doctype = lambda value: False
sys.modules[model_utils.__name__] = model_utils
path = Path(__file__).resolve().parents[1] / "frappe_whatsapp/utils/report_export.py"
spec = importlib.util.spec_from_file_location("report_export_under_test", path)
export = importlib.util.module_from_spec(spec)
spec.loader.exec_module(export)


class TestReportExport(unittest.TestCase):
    def setUp(self):
        self.report = types.SimpleNamespace(
            ref_doctype="Sales Invoice", report_type="Query Report", disabled=0,
            is_permitted=lambda: True, get_data=Mock(return_value=([{"fieldname": "name"}], [["INV-1"]])),
        )
        frappe.get_doc = Mock(return_value=self.report)
        frappe.has_permission = Mock(return_value=True)
        query_report.run = Mock(return_value={"columns": [{"fieldname": "name"}], "result": [{"name": "INV-1"}]})
        reportview.validate_args = Mock()
        reportview.execute = Mock(return_value=[{"name": "INV-1", "grand_total": 100}])
        self.builder = {
            "args": {"doctype": "Sales Invoice", "fields": ["name", "grand_total"], "filters": [["status", "=", "Paid"]]},
            "columns": [{"fieldname": "name", "label": "Invoice"}, {"fieldname": "grand_total", "label": "Total"}],
        }

    def test_any_app_query_report_uses_current_filters(self):
        columns, rows = export.execute_report("Other App Report", '{"company":"Mubtkir"}')
        query_report.run.assert_called_once_with("Other App Report", filters={"company": "Mubtkir"})
        self.assertEqual(rows[0]["name"], "INV-1")

    def test_script_and_custom_report_use_native_runner(self):
        for kind in ("Script Report", "Custom Report"):
            self.report.report_type = kind
            export.execute_report("Application Report", {})
        self.assertEqual(query_report.run.call_count, 2)

    def test_read_report_and_export_permissions_checked(self):
        export.execute_report("R", {})
        self.assertEqual([c.args[1] for c in frappe.has_permission.call_args_list], ["read", "report", "export"])

    def test_denied_permission_never_runs_report(self):
        frappe.has_permission.side_effect = PermissionError("denied")
        with self.assertRaises(PermissionError):
            export.execute_report("R", {})
        query_report.run.assert_not_called()

    def test_report_role_denial_and_disabled_report(self):
        self.report.is_permitted = lambda: False
        with self.assertRaises(ValueError): export.execute_report("R", {})
        self.report.is_permitted = lambda: True
        self.report.disabled = 1
        with self.assertRaises(ValueError): export.execute_report("R", {})
        query_report.run.assert_not_called()

    def test_prepared_report_not_ready_never_exports_blank_file(self):
        query_report.run.return_value = {"prepared_report": True, "doc": None}
        with self.assertRaisesRegex(ValueError, "prepared"):
            export.execute_report("R", {})

    def test_saved_builder_uses_native_permission_aware_runner(self):
        self.report.report_type = "Report Builder"
        export.execute_report("Saved Builder", {})
        self.report.get_data.assert_called_once_with(filters={}, limit=10001, user="report-user")
        query_report.run.assert_not_called()

    def test_unsaved_builder_uses_server_rows_and_live_filters(self):
        columns, rows = export.execute_report(None, {}, self.builder)
        reportview.validate_args.assert_called_once()
        self.assertEqual(reportview.execute.call_args.kwargs["filters"], [["status", "=", "Paid"]])
        self.assertEqual(rows[0]["grand_total"], 100)
        self.assertEqual([c["fieldname"] for c in columns], ["name", "grand_total"])

    def test_builder_drops_permission_bypass_identity_and_pagination(self):
        self.builder["args"].update(ignore_permissions=True, user="Administrator", run=0, start=500, page_length=0, as_list=True)
        export.execute_report(None, {}, self.builder)
        args = reportview.execute.call_args.kwargs
        for key in ("ignore_permissions", "user", "run", "as_list"):
            self.assertNotIn(key, args)
        self.assertEqual(args["start"], 0)
        self.assertEqual(args["page_length"], 10001)

    def test_builder_does_not_export_client_rows_or_unknown_columns(self):
        self.builder["rows"] = [{"name": "forged"}]
        self.builder["columns"].append({"fieldname": "secret", "label": "Secret"})
        columns, rows = export.execute_report(None, {}, self.builder)
        self.assertEqual(rows[0]["name"], "INV-1")
        self.assertNotIn("secret", [c["fieldname"] for c in columns])

    def test_builder_mismatched_report_or_type_rejected(self):
        with self.assertRaises(ValueError): export.execute_report("Query", {}, self.builder)
        self.report.report_type = "Report Builder"
        self.report.ref_doctype = "Customer"
        with self.assertRaises(ValueError): export.execute_report("Customer Builder", {}, self.builder)
        reportview.execute.assert_not_called()

    def test_virtual_builder_uses_controller_instead_of_database_table(self):
        base_document = types.ModuleType("frappe.model.base_document")
        get_list = Mock(return_value=[{"name": "virtual-row"}])
        base_document.get_controller = lambda doctype: types.SimpleNamespace(get_list=get_list)
        sys.modules[base_document.__name__] = base_document
        model_utils.is_virtual_doctype = lambda value: True
        try:
            columns, rows = export.execute_report(None, {}, self.builder)
            self.assertEqual(rows[0]["name"], "virtual-row")
            reportview.execute.assert_not_called()
        finally:
            model_utils.is_virtual_doctype = lambda value: False

    def test_scheduled_reports_use_owner_and_restore_user_after_failure(self):
        import test_delivery_safety as safety
        sf = safety.frappe
        sf.flags = Dict()
        sf.session = Dict(user="Administrator")
        sf.set_user = lambda user: sf.session.update(user=user)
        seen = []

        def send_now():
            seen.append(sf.session.user)
            raise RuntimeError("simulated render failure")

        scheduled = types.SimpleNamespace(owner="report-owner", is_due=lambda *a: True, send_now=send_now)
        sf.get_all = lambda *a, **kw: ["schedule"]
        sf.get_doc = lambda *a: scheduled
        sf.db = types.SimpleNamespace(get_value=lambda *a: True)
        sf.get_traceback = lambda: "test traceback"
        sf.log_error = Mock()
        safety.report.trigger_scheduled_reports()
        self.assertEqual(seen, ["report-owner"])
        self.assertEqual(sf.session.user, "Administrator")

    def test_row_limit_raises_instead_of_silently_truncating(self):
        query_report.run.return_value["result"] = [{}] * 10001
        with self.assertRaisesRegex(ValueError, "Narrow"):
            export.execute_report("R", {})
        reportview.execute.return_value = [{}] * 10001
        with self.assertRaisesRegex(ValueError, "Narrow"):
            export.execute_report(None, {}, self.builder)

    def test_malformed_filters_and_builder_arguments_rejected(self):
        for value in ("{", "[]", []):
            with self.assertRaises(ValueError): export.execute_report("R", value)
        with self.assertRaises(ValueError): export.execute_report(None, {}, {"args": [], "columns": []})
        query_report.run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
