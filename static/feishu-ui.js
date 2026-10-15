/* Personal Feishu settings are available to every authenticated member. */
(function (root) {
  "use strict";
  const F = root ? root.Feishu : require("./feishu.js");
  function mountPanel({
    doc,
    client,
    context,
    workspace,
    navigate,
    available = () => true,
  }) {
    const q = (s) => doc.querySelector(s),
      dialog = q("#feishuDialog"),
      message = q("#feishuMessage");
    let sequence = 0,
      busy = false;
    const actions = ["#feishuBind", "#feishuUnlink", "#feishuFinalize"];
    function disable() {
      actions.forEach((id) => (q(id).disabled = true));
      q("#feishuSubscriptions").replaceChildren();
      q("#feishuPassword").value = "";
      q("#feishuConfirm").checked = false;
    }
    function invalidate() {
      sequence++;
      client.invalidate();
      disable();
      message.textContent = "工作区已变化，请刷新状态。";
    }
    async function refresh() {
      const seq = ++sequence,
        generation = context();
      disable();
      if (!available()) {
        message.textContent = "正在确认工作区，请稍后刷新状态。";
        return;
      }
      message.textContent = "正在读取飞书状态…";
      try {
        const s = await client.refresh();
        if (seq !== sequence || generation !== context()) return;
        q("#feishuName").textContent =
          s.pending_intent === "bind"
            ? s.pending_binding?.name || "无法取得待绑定身份的已验证姓名"
            : s.binding?.name || "尚未绑定";
        if (s.enabled !== true || s.authenticated !== true) {
          message.textContent = s.enabled
            ? "请先登录当前账户。"
            : "飞书功能尚未启用。";
          return;
        }
        q("#feishuBind").disabled =
          busy || s.bound || s.pending_intent === "bind";
        q("#feishuUnlink").disabled = busy || !s.bound;
        q("#feishuFinalize").disabled =
          busy || s.pending_intent !== "bind" || !s.pending_binding?.name;
        message.textContent =
          s.pending_intent === "bind"
            ? "请核对以上飞书姓名，勾选确认后完成绑定。"
            : "通知默认关闭，仅订阅你可访问的当前工作区邮箱。";
        if (!s.bound) return;
        const items = await F.subscriptions(client, context);
        if (seq !== sequence || generation !== context()) return;
        for (const item of items) {
          if (item.workspace_id !== workspace()) continue;
          const label = doc.createElement("label"),
            input = doc.createElement("input"),
            text = doc.createElement("span");
          input.type = "checkbox";
          input.checked = item.enabled;
          input.disabled = busy;
          text.textContent = item.name;
          label.append(input, text);
          q("#feishuSubscriptions").append(label);
          input.onchange = () =>
            run(async () => {
              if (generation !== context() || seq !== sequence)
                throw F.failure("STALE");
              await F.subscribe(client, item.mailbox_id, input.checked);
            });
        }
      } catch (e) {
        if (seq === sequence) {
          disable();
          message.textContent = F.failure(e.code).message;
        }
      }
    }
    async function run(action) {
      if (busy) return;
      const generation = context();
      busy = true;
      actions.forEach((id) => (q(id).disabled = true));
      try {
        await action();
        if (generation !== context()) return;
        busy = false;
        await refresh();
      } catch (e) {
        if (generation !== context()) return;
        disable();
        message.textContent = F.failure(
          e.code,
        ).message; /* Never retry a mutation, including CSRF failures. */
      } finally {
        busy = false;
        /* Explicit refresh is required after a failure. */ if (
          generation === context() &&
          client.status &&
          client.status.authenticated === true &&
          available()
        ) {
          const s = client.status;
          q("#feishuBind").disabled =
            !!s.bound || s.pending_intent === "bind" || s.enabled !== true;
          q("#feishuUnlink").disabled = !s.bound || s.enabled !== true;
          q("#feishuFinalize").disabled =
            s.pending_intent !== "bind" ||
            !s.pending_binding?.name ||
            s.enabled !== true;
        }
      }
    }
    q("#feishuBind").onclick = () =>
      run(async () => {
        const result = await F.bind(
          client,
          q("#feishuPassword"),
          q("#feishuConfirm").checked,
        );
        const url = new URL(result.authorization_url);
        if (url.protocol !== "https:" || url.hostname !== "accounts.feishu.cn")
          throw F.failure("OAUTH_REJECTED");
        navigate(url.href);
      });
    q("#feishuUnlink").onclick = () =>
      run(() =>
        F.bind(client, q("#feishuPassword"), q("#feishuConfirm").checked, true),
      );
    q("#feishuFinalize").onclick = () =>
      run(() => F.finalize(client, q("#feishuConfirm").checked));
    q("#feishuRefresh").onclick = () => {
      if (!busy) return refresh();
    };
    q("#feishuClose").onclick = () => dialog.close();
    dialog.addEventListener("close", () => {
      q("#feishuPassword").value = "";
      q("#feishuConfirm").checked = false;
    });
    q("#feishuSettings").onclick = async () => {
      dialog.showModal();
      await refresh();
    };
    return {
      refresh,
      invalidate,
      open: async () => {
        dialog.showModal();
        await refresh();
      },
    };
  }
  if (typeof module !== "undefined") module.exports = { mountPanel };
  if (!root) return;
  const q = (s) => document.querySelector(s);
  let storage;
  try {
    storage = root.sessionStorage;
  } catch (_) {
    storage = {};
  }
  const link = F.createTicketLink(storage);
  link.capture(root.location.search);
  const query = new URLSearchParams(root.location.search),
    intent = query.get("feishu");
  // Remove routing hints after capturing, preventing reload/replay of failed links.
  if (query.has("ticket") || query.has("feishu") || query.has("workspace")) {
    query.delete("ticket");
    query.delete("feishu");
    query.delete("workspace");
    root.history.replaceState(
      null,
      "",
      root.location.pathname +
        (query.size ? "?" + query : "") +
        root.location.hash,
    );
  }
  if (q("#loginForm")) {
    const client = F.createClient({ fetch: root.fetch.bind(root) }),
      button = q("#feishuLogin"),
      status = q("#formStatus");
    button.onclick = () => {
      if (client.status?.enabled === true)
        root.location.assign("/api/auth/feishu/start");
    };
    void (async () => {
      try {
        const s = await client.refresh();
        button.hidden = s.enabled !== true;
        button.disabled = s.enabled !== true;
        if (intent === "complete") {
          if (s.pending_intent !== "login")
            throw F.failure("OAUTH_STATE_INVALID");
          button.disabled = true;
          await F.finalize(client);
          root.location.replace("/");
        }
      } catch (e) {
        button.disabled = true;
        if (intent) status.textContent = F.failure(e.code).message;
      }
    })();
    return;
  }
  const context = () => workspaceGeneration,
    client = F.createClient({ fetch: root.fetch.bind(root), context });
  const panel = mountPanel({
    doc: document,
    client,
    context,
    workspace: () => state.session?.user.workspace_id,
    navigate: (url) => root.location.assign(url),
    available: () =>
      workspaceSessionDefinitive && !state.workspaceSwitchPending,
  });
  let ready = false;
  root.FeishuUI = {
    invalidate: panel.invalidate,
    changed: () => {
      if (q("#feishuDialog").open) void panel.refresh();
      else {
        client.invalidate();
        if (workspaceSessionDefinitive) void client.refresh().catch(() => {});
      }
    },
    async ready() {
      if (ready) return;
      ready = true;
      try {
        if (intent === "confirm") await panel.open();
        await F.openLink(client, link, {
          switch: (operation) =>
            switchWorkspace(null, q("#workspaceSwitcher"), operation),
          workspace: () => state.session?.user.workspace_id,
          definitive: () =>
            workspaceSessionDefinitive && !state.workspaceSwitchPending,
          open: openTicket,
        });
      } catch (e) {
        link.clear();
        toast(F.failure(e.code).message);
      }
    },
  };
  if (state.session && refreshController) void root.FeishuUI.ready();
})(typeof window !== "undefined" ? window : null);
