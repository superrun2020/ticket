/* Feishu API boundary: no provider credentials, identities, or caller Origin. */
(function (root) {
  "use strict";
  const messages = {
    AUTH_REQUIRED: "登录已失效，请重新登录。",
    INVALID_CREDENTIALS: "当前账户密码不正确。",
    CSRF_REJECTED: "会话已变化，请刷新设置后重新确认操作。",
    ORIGIN_REJECTED: "当前站点无法完成此操作。",
    FEISHU_DISABLED: "飞书功能尚未启用。",
    ALREADY_BOUND: "已绑定；请先明确解绑，不能覆盖绑定。",
    BINDING_CONFLICT: "该飞书身份无法绑定此账户。",
    STALE: "工作区已变化，请重新打开设置。",
    CONFIRMATION_REQUIRED: "请明确确认此操作。",
    PENDING_NAME_REQUIRED: "无法确认待绑定的飞书姓名，请重新开始绑定。",
    TICKET_FORBIDDEN: "无法访问此工单。",
    TOO_MANY_ATTEMPTS: "尝试次数过多，请稍后再试。",
  };
  function failure(code) {
    return Object.assign(
      new Error(messages[code] || "操作未完成，请刷新状态后重试。"),
      { code },
    );
  }
  function createClient({ fetch, context = () => 0 }) {
    let status = null,
      epoch = 0,
      statusContext = null;
    async function request(url, options = {}) {
      let response;
      try {
        response = await fetch(url, {
          ...options,
          credentials: "same-origin",
          cache: "no-store",
          redirect: "error",
        });
      } catch (_) {
        throw failure("NETWORK");
      }
      let data;
      try {
        data = await response.json();
      } catch (_) {
        throw failure("RESPONSE");
      }
      if (!response.ok)
        throw failure(data?.detail?.error || data?.error || "REQUEST");
      return data;
    }
    async function refresh() {
      const generation = context(),
        seq = ++epoch;
      status = null;
      const data = await request("/api/auth/feishu/status");
      if (generation !== context() || seq !== epoch) throw failure("STALE");
      status = data;
      statusContext = generation;
      return data;
    }
    async function mutate(url, payload, method = "POST") {
      if (status && statusContext !== context()) throw failure("STALE");
      if (status?.enabled !== true) throw failure("FEISHU_DISABLED");
      if (!status.csrf_token) throw failure("CSRF_REJECTED");
      const generation = context();
      try {
        const data = await request(url, {
          method,
          headers: {
            "Content-Type": "application/json",
            "X-Feishu-CSRF": status.csrf_token,
          },
          body: JSON.stringify(payload),
        });
        if (generation !== context()) throw failure("STALE");
        return data;
      } catch (error) {
        status = null;
        throw error;
      }
    }
    return {
      refresh,
      mutate,
      request,
      get status() {
        return status;
      },
      invalidate() {
        epoch++;
        status = null;
      },
    };
  }
  async function bind(client, input, confirmed, unlink = false) {
    let password = input.value;
    input.value = "";
    try {
      if (confirmed !== true || !password)
        throw failure("CONFIRMATION_REQUIRED");
      if (!unlink && client.status?.bound) throw failure("ALREADY_BOUND");
      return await client.mutate(
        "/api/auth/feishu/" + (unlink ? "unlink" : "bind-start"),
        { password, confirm: true },
      );
    } finally {
      password = "";
    }
  }
  async function finalize(client, confirmed = false) {
    const s = client.status;
    if (s?.pending_intent === "bind") {
      if (confirmed !== true) throw failure("CONFIRMATION_REQUIRED");
      if (
        typeof s.pending_binding?.name !== "string" ||
        !s.pending_binding.name
      )
        throw failure("PENDING_NAME_REQUIRED");
    } else if (s?.pending_intent !== "login")
      throw failure("OAUTH_STATE_INVALID");
    return client.mutate("/api/auth/feishu/finalize", { confirm: true });
  }
  async function subscriptions(client, context) {
    const generation = context();
    const d = await client.request("/api/notifications/feishu/subscriptions");
    if (generation !== context()) throw failure("STALE");
    return (d.items || []).map((x) => ({ ...x, enabled: Boolean(x.enabled) }));
  }
  async function subscribe(client, id, enabled) {
    if (typeof enabled !== "boolean") throw failure("CONFIRMATION_REQUIRED");
    if (!client.status?.bound) throw failure("EXISTING_BINDING_REQUIRED");
    return client.mutate(
      "/api/notifications/feishu/subscriptions/" + encodeURIComponent(id),
      { enabled },
      "PUT",
    );
  }
  function createTicketLink(storage, now = Date.now) {
    const key = "ticket.pending-link";
    const valid = (id) =>
      typeof id === "string" && /^TKT-[0-9]{1,20}$/.test(id);
    function clear() {
      try {
        storage.removeItem(key);
      } catch (_) {}
    }
    function peek() {
      try {
        const x = JSON.parse(storage.getItem(key) || "null");
        if (
          x &&
          valid(x.id) &&
          Number.isFinite(x.at) &&
          now() - x.at >= 0 &&
          now() - x.at < 1800000
        )
          return x.id;
      } catch (_) {}
      clear();
      return null;
    }
    function capture(search) {
      const params = new URLSearchParams(search);
      if (!params.has("ticket")) return;
      clear();
      const id = params.get("ticket");
      if (params.getAll("ticket").length !== 1 || !valid(id)) return;
      try {
        storage.setItem(key, JSON.stringify({ id, at: now() }));
      } catch (_) {}
    }
    return {
      capture,
      peek,
      clear,
      take() {
        const id = peek();
        clear();
        return id;
      },
    };
  }
  async function openLink(client, link, adapter) {
    const id = link.take();
    if (!id) return;
    let result, error;
    await adapter.switch(async () => {
      try {
        await client.refresh();
        result = await client.mutate("/api/notifications/feishu/open-ticket", {
          ticket_id: id,
        });
      } catch (e) {
        error = e;
        throw e;
      }
    });
    if (error) throw error;
    if (
      !result ||
      result.ticket_id !== id ||
      !adapter.definitive() ||
      adapter.workspace() !== result.workspace_id
    )
      throw failure("STALE");
    await adapter.open(id);
  }
  const exported = {
    createClient,
    failure,
    bind,
    finalize,
    subscriptions,
    subscribe,
    createTicketLink,
    openLink,
  };
  if (typeof module !== "undefined") module.exports = exported;
  if (root) root.Feishu = exported;
})(typeof window !== "undefined" ? window : null);
