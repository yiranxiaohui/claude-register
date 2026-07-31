const j = async (r) => {
  if (!r.ok) {
    const err = new Error(`http ${r.status}`);
    err.status = r.status;
    try {
      err.body = await r.json();
    } catch {
      /* no json body */
    }
    throw err;
  }
  return r.json();
};

export const api = {
  login: (password) =>
    fetch("/api/login", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ password }),
    }).then(j),

  getConfig: () => fetch("/api/config").then(j),

  putConfig: (body) =>
    fetch("/api/config", {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    }).then(j),

  startRun: (email, domain) =>
    fetch("/api/runs", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ email, domain }),
    }).then(j),

  listRuns: () => fetch("/api/runs").then(j),

  runDetail: (id) => fetch(`/api/runs/${id}`).then(j),

  listAccounts: () => fetch("/api/accounts").then(j),

  exportAccountsText: () =>
    fetch("/api/accounts/export").then((r) => {
      if (!r.ok) {
        const err = new Error(`http ${r.status}`);
        err.status = r.status;
        throw err;
      }
      return r.text();
    }),

  accountUpdate: (email, fields) =>
    fetch(`/api/accounts/${encodeURIComponent(email)}`, {
      method: "PATCH",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(fields),
    }).then(j),

  accountDelete: (email) =>
    fetch(`/api/accounts/${encodeURIComponent(email)}`, {
      method: "DELETE",
    }).then(j),

  rerun: (email) =>
    fetch(`/api/accounts/${encodeURIComponent(email)}/rerun`, {
      method: "POST",
    }).then(j),

  checkAccount: (email) =>
    fetch(`/api/accounts/${encodeURIComponent(email)}/check`, {
      method: "POST",
    }).then(j),

  xuiTest: (node) =>
    fetch("/api/xui/test", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(node),
    }).then(j),

  xuiCleanup: () =>
    fetch("/api/xui/cleanup", { method: "POST" }).then(j),

  takeoverStart: (email) =>
    fetch("/api/takeover/start", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ email }),
    }).then(j),

  takeoverStop: () =>
    fetch("/api/takeover/stop", { method: "POST" }).then(j),

  takeoverStatus: () => fetch("/api/takeover").then(j),
};
