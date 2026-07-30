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

  rerun: (email) =>
    fetch(`/api/accounts/${encodeURIComponent(email)}/rerun`, {
      method: "POST",
    }).then(j),
};
