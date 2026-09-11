// pages/whatsapp/new-instance.jsx
//
// Create a new WhatsApp instance.
//
// NOTE: This is a Next.js (pages router) page intended for the project's
// separate Next.js frontend. The `mu-wats-api` repo is a pure Frappe (Python)
// app and has no Next.js build pipeline, so this file is provided as a
// reference to be moved into the frontend project. If you use the app router,
// place the equivalent component at `app/whatsapp/new-instance/page.jsx`.
//
// It calls the whitelisted Frappe method:
//   POST /api/method/frappe_whatsapp.api.create_whatsapp_instance
// with { evolution_server, linked_user, phone_number }.
//
// Configure the Frappe base URL via NEXT_PUBLIC_FRAPPE_URL (defaults to same
// origin). Authentication relies on the logged-in Frappe session cookie
// (credentials: "include"); alternatively set NEXT_PUBLIC_FRAPPE_TOKEN to an
// "api_key:api_secret" token.

import { useEffect, useState } from "react";
import { useRouter } from "next/router";

const FRAPPE_URL = process.env.NEXT_PUBLIC_FRAPPE_URL || "";
const FRAPPE_TOKEN = process.env.NEXT_PUBLIC_FRAPPE_TOKEN || "";

function authHeaders() {
  const headers = { "Content-Type": "application/json" };
  if (FRAPPE_TOKEN) {
    headers["Authorization"] = `token ${FRAPPE_TOKEN}`;
  }
  return headers;
}

// Fetch a list of records for a doctype via Frappe's REST client method.
async function fetchList(doctype, { filters = [], fields = ["name"] } = {}) {
  const params = new URLSearchParams({
    doctype,
    fields: JSON.stringify(fields),
    filters: JSON.stringify(filters),
    limit_page_length: "0",
  });
  const res = await fetch(
    `${FRAPPE_URL}/api/method/frappe.client.get_list?${params.toString()}`,
    { method: "GET", headers: authHeaders(), credentials: "include" }
  );
  if (!res.ok) {
    throw new Error(`Failed to load ${doctype} (HTTP ${res.status})`);
  }
  const json = await res.json();
  return json.message || [];
}

export default function NewInstancePage() {
  const router = useRouter();

  const [servers, setServers] = useState([]);
  const [users, setUsers] = useState([]);
  const [form, setForm] = useState({
    evolution_server: "",
    phone_number: "",
    linked_user: "",
  });
  const [loadingLists, setLoadingLists] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  // Load the Evolution Server and User dropdowns from Frappe.
  useEffect(() => {
    let active = true;
    (async () => {
      try {
        const [serverList, userList] = await Promise.all([
          fetchList("Evolution Server", {
            filters: [["is_active", "=", 1]],
            fields: ["name", "server_label"],
          }),
          fetchList("User", {
            filters: [["enabled", "=", 1]],
            fields: ["name", "full_name"],
          }),
        ]);
        if (!active) return;
        setServers(serverList);
        setUsers(userList);
      } catch (err) {
        if (active) setError(err.message || "Failed to load form data.");
      } finally {
        if (active) setLoadingLists(false);
      }
    })();
    return () => {
      active = false;
    };
  }, []);

  function handleChange(e) {
    const { name, value } = e.target;
    setForm((prev) => ({ ...prev, [name]: value }));
  }

  async function handleSubmit(e) {
    e.preventDefault();
    setError("");

    if (!form.evolution_server || !form.linked_user) {
      setError("Please select an Evolution Server and a User.");
      return;
    }

    setSubmitting(true);
    try {
      const res = await fetch(
        `${FRAPPE_URL}/api/method/frappe_whatsapp.api.create_whatsapp_instance`,
        {
          method: "POST",
          headers: authHeaders(),
          credentials: "include",
          body: JSON.stringify({
            evolution_server: form.evolution_server,
            linked_user: form.linked_user,
            phone_number: form.phone_number,
          }),
        }
      );

      const json = await res.json().catch(() => ({}));

      if (!res.ok) {
        // Frappe returns error details in _server_messages or exception.
        const serverMessage = parseFrappeError(json);
        throw new Error(serverMessage || `Request failed (HTTP ${res.status})`);
      }

      const result = json.message || {};
      if (!result.instance_name) {
        throw new Error("The server did not return an instance name.");
      }

      // On success, redirect to the instance detail page (shows the QR code).
      router.push(`/whatsapp/instance/${encodeURIComponent(result.instance_name)}`);
    } catch (err) {
      setError(err.message || "Something went wrong while creating the instance.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div style={styles.page}>
      <div style={styles.card}>
        <h1 style={styles.title}>Create WhatsApp Instance</h1>
        <p style={styles.subtitle}>
          Provision a new WhatsApp instance on the selected Evolution server and
          assign it to a user.
        </p>

        {error ? (
          <div style={styles.error} role="alert">
            {error}
          </div>
        ) : null}

        <form onSubmit={handleSubmit}>
          <label style={styles.label} htmlFor="evolution_server">
            Evolution Server
          </label>
          <select
            id="evolution_server"
            name="evolution_server"
            value={form.evolution_server}
            onChange={handleChange}
            style={styles.input}
            disabled={loadingLists || submitting}
            required
          >
            <option value="">— Select a server —</option>
            {servers.map((s) => (
              <option key={s.name} value={s.name}>
                {s.server_label || s.name}
              </option>
            ))}
          </select>

          <label style={styles.label} htmlFor="phone_number">
            Phone Number
          </label>
          <input
            id="phone_number"
            name="phone_number"
            type="text"
            value={form.phone_number}
            onChange={handleChange}
            style={styles.input}
            placeholder="e.g. 9665XXXXXXXX"
            disabled={submitting}
          />

          <label style={styles.label} htmlFor="linked_user">
            Assign to User
          </label>
          <select
            id="linked_user"
            name="linked_user"
            value={form.linked_user}
            onChange={handleChange}
            style={styles.input}
            disabled={loadingLists || submitting}
            required
          >
            <option value="">— Select a user —</option>
            {users.map((u) => (
              <option key={u.name} value={u.name}>
                {u.full_name ? `${u.full_name} (${u.name})` : u.name}
              </option>
            ))}
          </select>

          <button type="submit" style={styles.button} disabled={submitting || loadingLists}>
            {submitting ? "Creating…" : "Create Instance"}
          </button>
        </form>
      </div>
    </div>
  );
}

// Extract a human-readable message from a Frappe error response.
function parseFrappeError(json) {
  if (!json) return "";
  if (json.exception) return String(json.exception);
  const raw = json._server_messages;
  if (raw) {
    try {
      const messages = JSON.parse(raw);
      const first = JSON.parse(messages[0]);
      return first.message || messages[0];
    } catch (_e) {
      return raw;
    }
  }
  return json.message || "";
}

const styles = {
  page: {
    minHeight: "100vh",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    background: "#f5f6f8",
    padding: "24px",
    fontFamily: "system-ui, -apple-system, Segoe UI, Roboto, sans-serif",
  },
  card: {
    width: "100%",
    maxWidth: "440px",
    background: "#fff",
    borderRadius: "12px",
    boxShadow: "0 2px 12px rgba(0,0,0,0.08)",
    padding: "28px",
  },
  title: { margin: "0 0 6px", fontSize: "22px", color: "#1f2933" },
  subtitle: { margin: "0 0 20px", fontSize: "14px", color: "#616e7c" },
  label: {
    display: "block",
    margin: "14px 0 6px",
    fontSize: "13px",
    fontWeight: 600,
    color: "#3e4c59",
  },
  input: {
    width: "100%",
    padding: "10px 12px",
    fontSize: "14px",
    border: "1px solid #cbd2d9",
    borderRadius: "8px",
    boxSizing: "border-box",
    background: "#fff",
  },
  button: {
    marginTop: "22px",
    width: "100%",
    padding: "12px",
    fontSize: "15px",
    fontWeight: 600,
    color: "#fff",
    background: "#25D366",
    border: "none",
    borderRadius: "8px",
    cursor: "pointer",
  },
  error: {
    background: "#fdecea",
    color: "#b71c1c",
    border: "1px solid #f5c6cb",
    borderRadius: "8px",
    padding: "10px 12px",
    fontSize: "13px",
    marginBottom: "12px",
  },
};
