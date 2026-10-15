// Offline regression tests of the actual global toolbar integration.
const fs = require("node:fs");
const vm = require("node:vm");
const assert = require("node:assert/strict");
const source = fs.readFileSync(require("node:path").join(__dirname, "../frappe_whatsapp/public/js/wa_report.js"), "utf8");
let tests = 0;
function environment(route) {
    const state = {route, timers: new Map(), calls: [], dialogs: [], messages: [], listeners: [], observers: []};
    let sequence = 0;
    const page = () => ({
        buttons: [], inner_toolbar: [{}],
        remove_inner_button() { this.buttons.forEach(b => b[0].isConnected = false); this.buttons = []; },
        add_inner_button(label, click) { const b = [{isConnected: true}]; b.click = click; this.buttons.push(b); return b; },
    });
    state.page = page;
    class Dialog {
        constructor(config) { this.config = config; this.values = {report_format: "PDF", mobile_number: "966501234567"}; state.dialogs.push(this); }
        get_values() { return this.values; }
        show() {}
        hide() {}
    }
    class Observer {
        constructor(callback) { this.callback = callback; state.observers.push(this); }
        observe() {}
        disconnect() {}
    }
    const context = {
        console, Date, Math, JSON, MutationObserver: Observer,
        __: text => text,
        setInterval: fn => { const id = ++sequence; state.timers.set(id, fn); return id; },
        clearInterval: id => state.timers.delete(id), setTimeout: fn => fn(),
        frappe_whatsapp: {},
        frappe: {
            provide() {}, get_route: () => state.route,
            router: {on: (name, fn) => state.listeners.push(fn)}, ui: {Dialog},
            call: args => state.calls.push(args), msgprint: text => state.messages.push(text),
            show_alert() {}, set_route() {},
        },
    };
    context.window = context;
    context.crypto = {randomUUID: () => "dialog-request-id"};
    vm.createContext(context);
    state.context = context;
    state.load = () => vm.runInContext(source, context);
    state.tick = () => [...state.timers.values()].forEach(fn => fn());
    state.change = newRoute => { state.route = newRoute; state.listeners.forEach(fn => fn()); };
    state.query = name => ({page: page(), report_name: name, get_filter_values: () => ({company: "Mubtkir", from_date: "2026-10-01"})});
    return state;
}
function test(name, fn) { fn(); tests++; console.log("PASS", name); }

test("initial direct query report loads without requiring a route change", () => {
    const e = environment(["query-report", "External App Report"]);
    e.context.frappe.query_report = e.query("External App Report"); e.load();
    assert.equal(e.context.frappe.query_report.page.buttons.length, 1);
    e.load(); assert.equal(e.context.frappe.query_report.page.buttons.length, 1);
});
test("stale report is ignored until the requested report becomes ready", () => {
    const e = environment(["query-report", "New Report"]);
    const old = e.query("Old Report"); e.context.frappe.query_report = old; e.load();
    assert.equal(old.page.buttons.length, 0);
    const next = e.query("New Report"); e.context.frappe.query_report = next; e.tick();
    assert.equal(next.page.buttons.length, 1);
});
test("query send uses current filters and a stable request token", () => {
    const e = environment(["query-report", "Third Party Report"]);
    const qr = e.query("Third Party Report"); e.context.frappe.query_report = qr; e.load(); qr.page.buttons[0].click();
    const d = e.dialogs[0]; d.config.primary_action(); d.config.primary_action();
    assert.equal(e.calls.length, 2);
    assert.equal(e.calls[0].args.report, "Third Party Report");
    assert.equal(JSON.parse(e.calls[0].args.filters).company, "Mubtkir");
    assert.equal(e.calls[0].args.operation_key, e.calls[1].args.operation_key);
});
test("a dialog from a previous route cannot send the wrong report", () => {
    const e = environment(["query-report", "One"]);
    const qr = e.query("One"); e.context.frappe.query_report = qr; e.load(); qr.page.buttons[0].click();
    e.change(["query-report", "Two"]); e.dialogs[0].config.primary_action();
    assert.equal(e.calls.length, 0); assert.equal(e.messages.length, 1);
});
test("missing recipient or invalid dialog values never send", () => {
    const e = environment(["query-report", "One"]);
    const qr = e.query("One"); e.context.frappe.query_report = qr; e.load(); qr.page.buttons[0].click();
    const d = e.dialogs[0]; d.values = {}; d.config.primary_action();
    d.values = null; d.config.primary_action(); assert.equal(e.calls.length, 0);
});
test("Report Builder sends query arguments and selected columns, not client rows", () => {
    const e = environment(["List", "Sales Invoice", "Report"]);
    const view = {page: e.page(), doctype: "Sales Invoice", view_name: "Report", columns: [{id: "name", docfield: {label: "Invoice"}}], get_args: () => ({doctype: "Sales Invoice", filters: [["status", "=", "Paid"]], fields: ["name"]})};
    e.context.cur_list = view; e.load(); view.page.buttons[0].click();
    const d = e.dialogs[0]; d.config.primary_action();
    const spec = JSON.parse(e.calls[0].args.builder_spec);
    assert.equal(spec.args.doctype, "Sales Invoice"); assert.equal(spec.columns[0].fieldname, "name");
    assert.equal(spec.rows, undefined);
    const count = e.calls.length; d.config.secondary_action(); assert.equal(e.calls.length, count);
});
test("saved Report Builder retains its name for scheduling", () => {
    const e = environment(["List", "Sales Invoice", "Report", "My Invoices"]);
    const view = {page: e.page(), doctype: "Sales Invoice", view: "Report", report_name: "My Invoices", columns: [{id: "name"}], get_args: () => ({doctype: "Sales Invoice", fields: ["name"]})};
    e.context.cur_list = view; e.load(); view.page.buttons[0].click(); e.dialogs[0].config.secondary_action();
    assert.equal(e.calls[0].args.report, "My Invoices");
    assert.ok(e.calls[0].method.endsWith("create_scheduled_report"));
});
test("toolbar rebuild restores exactly one button", () => {
    const e = environment(["query-report", "One"]);
    const qr = e.query("One"); e.context.frappe.query_report = qr; e.load();
    qr.page.remove_inner_button(); e.observers[0].callback(); assert.equal(qr.page.buttons.length, 1);
});
test("leaving reports cancels pending timers", () => {
    const e = environment(["query-report", "Pending"]); e.load();
    e.change(["Workspaces", "Home"]); assert.equal(e.timers.size, 0);
});
test("custom app pages can opt into a registered report", () => {
    const e = environment(["custom-page"]); e.load(); const page = e.page();
    e.context.frappe_whatsapp.report_share.attach({page, report_name: "App Report", get_filters: () => ({})});
    assert.equal(page.buttons.length, 1);
});
console.log(`${tests} report UI tests passed`);
