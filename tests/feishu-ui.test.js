const test = require("node:test"),
  assert = require("node:assert/strict"),
  fs = require("node:fs");
const F = require("../static/feishu.js");
function setup() {
  const path = require("node:path").join(__dirname, "../static/feishu-ui.js");
  const UI = fs.existsSync(path) ? require(path) : {};
  assert.equal(typeof UI.mountPanel, "function");
  const nodes = new Map();
  const node = () => ({
    hidden: false,
    disabled: false,
    checked: false,
    value: "",
    textContent: "",
    children: [],
    append(...xs) {
      this.children.push(...xs);
    },
    replaceChildren(...xs) {
      this.children = xs;
    },
    addEventListener() {},
    showModal() {
      this.open = true;
    },
    close() {
      this.open = false;
    },
  });
  const doc = {
    querySelector(s) {
      if (!nodes.has(s)) nodes.set(s, node());
      return nodes.get(s);
    },
    createElement: node,
  };
  let gen = 0;
  let s = {
    enabled: true,
    authenticated: true,
    bound: false,
    csrf_token: "t",
    pending_intent: "bind",
    pending_binding: { name: "<img onerror=evil>" },
  };
  const calls = [];
  const c = F.createClient({
    fetch: async (url, o) => {
      calls.push([url, o]);
      return {
        ok: true,
        json: async () =>
          url.endsWith("/status")
            ? s
            : {
                items: [
                  {
                    mailbox_id: "m",
                    name: "Inbox",
                    workspace_id: "A",
                    enabled: 0,
                  },
                ],
              },
      };
    },
  });
  const panel = UI.mountPanel({
    doc,
    client: c,
    context: () => gen,
    workspace: () => "A",
    navigate() {},
  });
  return { panel, doc, calls, setStatus: (x) => (s = x), switch: () => gen++ };
}
test("personal panel renders verified pending name as text, defaults subscriptions off and requires explicit confirmation", async () => {
  const h = setup();
  await h.panel.refresh();
  assert.equal(
    h.doc.querySelector("#feishuName").textContent,
    "<img onerror=evil>",
  );
  assert.equal(h.doc.querySelector("#feishuFinalize").disabled, false);
  await h.doc.querySelector("#feishuFinalize").onclick();
  assert.equal(h.calls.filter((x) => x[1].method === "POST").length, 0);
  h.setStatus({
    enabled: true,
    authenticated: true,
    bound: true,
    binding: { name: "Bound" },
    csrf_token: "t",
  });
  await h.panel.refresh();
  const label = h.doc.querySelector("#feishuSubscriptions").children[0];
  assert.equal(label.children[0].checked, false);
});
test("late mutation failure cannot replace new workspace panel state", async () => {
  const h = setup();
  await h.panel.refresh();
  let reject;
  const old = h.doc.querySelector("#feishuFinalize");
  h.doc.querySelector("#feishuConfirm").checked = true;
  const original = F.finalize;
  F.finalize = () => new Promise((_, r) => (reject = r));
  try {
    const p = old.onclick();
    h.switch();
    h.panel.invalidate();
    h.doc.querySelector("#feishuMessage").textContent = "new workspace";
    reject(F.failure("STALE"));
    await p;
    assert.equal(
      h.doc.querySelector("#feishuMessage").textContent,
      "new workspace",
    );
  } finally {
    F.finalize = original;
  }
});
test("disabled or unavailable status cannot break original UI and disables personal mutations", async () => {
  const h = setup();
  h.setStatus({ enabled: "true", authenticated: true });
  await h.panel.refresh();
  assert.equal(h.doc.querySelector("#feishuBind").disabled, true);
  assert.equal(h.doc.querySelector("#feishuFinalize").disabled, true);
  assert.equal(h.doc.querySelector("#feishuSubscriptions").children.length, 0);
});
