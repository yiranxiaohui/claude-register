// 会话失效（401）全局广播：App 监听后踢回登录页，
// 避免密码改了/cookie 过期后面板还停在空壳界面上。
export const SESSION_EXPIRED_EVENT = "cr:session-expired";

// 登录请求自身的 401 是「密码错」，不是会话失效，不广播。
const AUTH_FREE = new Set(["/api/login", "/api/logout"]);

const j = async (r) => {
  if (!r.ok) {
    if (r.status === 401 && !AUTH_FREE.has(new URL(r.url, location.origin).pathname)) {
      window.dispatchEvent(new CustomEvent(SESSION_EXPIRED_EVENT));
    }
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

  logout: () => fetch("/api/logout", { method: "POST" }).then(j),

  putConfig: (body) =>
    fetch("/api/config", {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    }).then(j),

  startRun: (email, domain, proxyId) =>
    fetch("/api/runs", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ email, domain, proxy_id: proxyId }),
    }).then(j),

  listRuns: () => fetch("/api/runs").then(j),

  runDetail: (id) => fetch(`/api/runs/${id}`).then(j),

  listAccounts: () => fetch("/api/accounts").then(j),

  exportAccountsText: (params) =>
    fetch(`/api/accounts/export${params ? `?${new URLSearchParams(params)}` : ""}`).then((r) => {
      if (!r.ok) {
        if (r.status === 401) window.dispatchEvent(new CustomEvent(SESSION_EXPIRED_EVENT));
        const err = new Error(`http ${r.status}`);
        err.status = r.status;
        throw err;
      }
      return r.text();
    }),

  exportFields: () => fetch("/api/export/fields").then(j),

  rotateApiKey: () => fetch("/api/api-key", { method: "POST" }).then(j),

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

  takeoverRelogin: () =>
    fetch("/api/takeover/relogin", { method: "POST" }).then(j),

  takeoverStatus: () => fetch("/api/takeover").then(j),
};
