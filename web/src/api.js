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

export async function prepareOAuthUpload(files) {
  if (!files.length) throw new Error("请选择至少一个 JSON 文件");
  if (files.length > 1000) throw new Error("每次最多上传 1000 个文件");
  const limit = 10 * 1024 * 1024;
  const tooLarge = "上传内容不能超过 10 MB，请分批选择文件";
  if (files.reduce((sum, file) => sum + file.size, 0) > limit) throw new Error(tooLarge);
  if (files.length === 1) return { body: files[0], filename: files[0].name };
  const entries = await Promise.all(files.map(async (file) => {
    try {
      const content = new TextDecoder("utf-8", { fatal: true }).decode(await file.arrayBuffer());
      return { filename: file.name, content };
    } catch {
      throw new Error(`文件「${file.name}」读取失败，请使用 UTF-8 编码的 JSON 文件`);
    }
  }));
  const body = new Blob([JSON.stringify({ files: entries })], {
    type: "application/vnd.claude-register.oauth-files+json",
  });
  if (body.size > limit) throw new Error(tooLarge);
  return { body };
}

function uploadOAuth(path, upload, params = {}) {
  if (upload.filename) params.filename = upload.filename;
  return fetch(`${path}?${new URLSearchParams(params)}`, {
    method: "POST",
    headers: { "content-type": upload.filename ? "application/json" : upload.body.type },
    body: upload.body,
  }).then(j);
}

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

  importAccounts: (body) =>
    fetch("/api/accounts/import", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    }).then(j),

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

  accountCreate: (fields) =>
    fetch("/api/accounts", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(fields),
    }).then(j),

  accountsBatchDelete: (emails) =>
    fetch("/api/accounts/batch-delete", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ emails }),
    }).then(j),

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

  setAccountClaimed: (email, claimed) =>
    fetch(`/api/accounts/${encodeURIComponent(email)}/claimed`, {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ claimed }),
    }).then(j),

  checkAccount: (email) =>
    fetch(`/api/accounts/${encodeURIComponent(email)}/check`, {
      method: "POST",
    }).then(j),

  accountOAuthOrgs: (email, preferred) =>
    fetch(
      `/api/accounts/${encodeURIComponent(email)}/oauth/organizations${
        preferred ? `?${new URLSearchParams({ preferred })}` : ""
      }`,
    ).then(j),

  // orgUuid 为空时由后端按「上次授权的组织 → team → 第一个」选择
  accountOAuth: (email, orgUuid) =>
    fetch(`/api/accounts/${encodeURIComponent(email)}/oauth`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(orgUuid ? { org_uuid: orgUuid } : {}),
    }).then(j),

  sub2apiAccounts: () => fetch("/api/sub2api/accounts").then(j),

  sub2apiImportPreview: (upload) => uploadOAuth("/api/sub2api/import/preview", upload),

  sub2apiImport: (upload, bindDefaultGroup) =>
    uploadOAuth("/api/sub2api/import", upload, { bind_default_group: String(bindDefaultGroup) }),

  sub2apiReauth: (id, orgUuid) =>
    fetch(`/api/sub2api/accounts/${encodeURIComponent(id)}/reauth`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(orgUuid ? { org_uuid: orgUuid } : {}),
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

  takeoverManual: (body) =>
    fetch("/api/takeover/manual", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    }).then(j),

  takeoverCapture: () =>
    fetch("/api/takeover/capture", { method: "POST" }).then(j),

  takeoverStop: () =>
    fetch("/api/takeover/stop", { method: "POST" }).then(j),

  takeoverRelogin: () =>
    fetch("/api/takeover/relogin", { method: "POST" }).then(j),

  takeoverStatus: () => fetch("/api/takeover").then(j),
};
