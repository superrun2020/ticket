const test = require("node:test"),
  assert = require("node:assert/strict"),
  fs = require("node:fs");
const path = require("node:path").join(__dirname, "../static/feishu.js");
const F = fs.existsSync(path) ? require(path) : {};
test("binding consumes password immediately, requires confirmation and never overwrites; finalize requires verified name", async () => {
  const h = fixture();
  await h.client.refresh();
  const input = { value: "synthetic-only" };
  await assert.rejects(F.bind(h.client, input, false));
  assert.equal(input.value, "");
  input.value = "synthetic-only";
  await F.bind(h.client, input, true);
  assert.equal(input.value, "");
  assert.deepEqual(JSON.parse(h.calls.at(-1).options.body), {
    password: "synthetic-only",
    confirm: true,
  });
  h.setStatus({ enabled: true, bound: true, csrf_token: "t" });
  await h.client.refresh();
  await assert.rejects(F.bind(h.client, { value: "synthetic-only" }, true));
  await assert.rejects(F.finalize(h.client, true));
});
test("subscriptions use authorized read list and explicit booleans; stale workspace response is rejected", async () => {
  let gen = 0,
    resolve;
  const calls = [];
  const c = F.createClient({
    context: () => gen,
    fetch: async (url, o) => {
      calls.push([url, o]);
      return {
        ok: true,
        json: async () =>
          url.endsWith("/status")
            ? { enabled: true, bound: true, csrf_token: "x" }
            : url.endsWith("/subscriptions")
              ? await new Promise((r) => (resolve = r))
              : { ok: true },
      };
    },
  });
  await c.refresh();
  const pending = F.subscriptions(c, () => gen);
  await new Promise((r) => setImmediate(r));
  gen++;
  resolve({ items: [{ mailbox_id: "a", enabled: 0 }] });
  await assert.rejects(pending);
  await assert.rejects(F.subscribe(c, "a", "true"));
  await c.refresh();
  await F.subscribe(c, "a", false);
  assert.deepEqual(JSON.parse(calls.at(-1)[1].body), { enabled: false });
});
test("CSRF failure invalidates token without retrying mutation or leaking server text", async () => {
  let n = 0;
  const c = F.createClient({
    fetch: async () => ({
      ok: ++n === 1,
      json: async () =>
        n === 1
          ? { enabled: true, csrf_token: "x" }
          : { detail: { error: "CSRF_REJECTED", secret: "never show" } },
    }),
  });
  await c.refresh();
  await assert.rejects(
    c.mutate("/api/auth/feishu/finalize", { confirm: true }),
    (e) => e.code === "CSRF_REJECTED" && !e.message.includes("never"),
  );
  await assert.rejects(
    c.mutate("/api/auth/feishu/finalize", { confirm: true }),
  );
  assert.equal(n, 2);
});
test("deep link survives auth transitions, rejects malformed/stale storage and consumes once before authorization", async () => {
  const values = new Map();
  const storage = {
    getItem: (k) => values.get(k),
    setItem: (k, v) => values.set(k, v),
    removeItem: (k) => values.delete(k),
  };
  const link = F.createTicketLink(storage, () => 1000);
  link.capture("?ticket=TKT-123&workspace=evil");
  assert.equal(link.peek(), "TKT-123");
  link.capture("?feishu=complete");
  assert.equal(link.peek(), "TKT-123");
  assert.equal(link.take(), "TKT-123");
  assert.equal(link.take(), null);
  link.capture("?ticket=TKT-123/evil");
  assert.equal(link.peek(), null);
  link.capture("?ticket=TKT-1");
  const late = F.createTicketLink(storage, () => 99999999);
  assert.equal(late.take(), null);
});
test("a token from a prior workspace cannot mutate even after A B A; late status cannot restore it", async () => {
  const h = fixture();
  await h.client.refresh();
  h.switch();
  await assert.rejects(
    h.client.mutate("/api/auth/feishu/finalize", { confirm: true }),
    (e) => e.code === "STALE",
  );
  assert.equal(h.calls.length, 1);
});
test("login finalization is automatic only for verified login intent; binding requires explicit checked confirmation", async () => {
  const h = fixture();
  h.setStatus({ enabled: true, csrf_token: "x", pending_intent: "login" });
  await h.client.refresh();
  await F.finalize(h.client);
  h.setStatus({
    enabled: true,
    csrf_token: "x",
    pending_intent: "bind",
    pending_binding: { name: "<unsafe>" },
  });
  await h.client.refresh();
  await assert.rejects(F.finalize(h.client));
  await F.finalize(h.client, true);
  assert.equal(h.calls.filter((c) => c.options.method === "POST").length, 2);
});
test("deep link authorizes then reconciles workspace before detail; rejected or mismatched links never fetch detail", async () => {
  for (const mode of ["ok", "forbidden", "mismatch"]) {
    const order = [];
    const h = fixture();
    const link = { take: () => "TKT-42" };
    const client = {
      refresh: async () => order.push("status"),
      mutate: async (url, payload) => {
        order.push("authorize");
        assert.deepEqual(payload, { ticket_id: "TKT-42" });
        if (mode === "forbidden") throw Error("denied");
        return { ticket_id: "TKT-42", workspace_id: "B" };
      },
    };
    const adapter = {
      switch: async (operation) => {
        try {
          await operation();
        } catch (_) {}
        order.push("reconcile");
      },
      workspace: () => (mode === "mismatch" ? "A" : "B"),
      definitive: () => true,
      open: async (id) => order.push("detail:" + id),
    };
    if (mode === "ok") await F.openLink(client, link, adapter);
    else await assert.rejects(F.openLink(client, link, adapter));
    assert.deepEqual(
      order,
      mode === "ok"
        ? ["status", "authorize", "reconcile", "detail:TKT-42"]
        : ["status", "authorize", "reconcile"],
    );
  }
});
test("unlink clears the password even on rejection, has no identity fields and never retries", async () => {
  let calls = 0,
    payload;
  const client = F.createClient({
    fetch: async (url, o) => {
      calls++;
      payload = o.body;
      return {
        ok: url.endsWith("/status"),
        json: async () =>
          url.endsWith("/status")
            ? {
                enabled: true,
                authenticated: true,
                bound: true,
                csrf_token: "x",
              }
            : { detail: { error: "INVALID_CREDENTIALS" } },
      };
    },
  });
  await client.refresh();
  const input = { value: "synthetic-not-real" };
  const pending = F.bind(client, input, true, true);
  assert.equal(input.value, "");
  await assert.rejects(pending, (e) => e.code === "INVALID_CREDENTIALS");
  assert.deepEqual(JSON.parse(payload), {
    password: "synthetic-not-real",
    confirm: true,
  });
  assert.equal(calls, 2);
});
test("status response completing after invalidation cannot restore an obsolete token", async () => {
  let resolve;
  const client = F.createClient({
    fetch: async () => ({
      ok: true,
      json: () => new Promise((r) => (resolve = r)),
    }),
  });
  const pending = client.refresh();
  await new Promise((r) => setImmediate(r));
  client.invalidate();
  resolve({ enabled: true, csrf_token: "old" });
  await assert.rejects(pending, (e) => e.code === "STALE");
  assert.equal(client.status, null);
});
function fixture() {
  let generation = 0;
  const calls = [];
  let status = {
    enabled: true,
    authenticated: true,
    csrf_token: "token",
    bound: false,
  };
  const client = F.createClient({
    fetch: async (url, options) => {
      calls.push({ url, options });
      return {
        ok: true,
        json: async () => (url.endsWith("/status") ? status : { ok: true }),
      };
    },
    context: () => generation,
  });
  return {
    client,
    calls,
    setStatus: (s) => (status = s),
    switch: () => generation++,
  };
}
test("status gates mutations strictly and same-origin CSRF has no caller identity or Origin override", async () => {
  assert.equal(typeof F.createClient, "function");
  const h = fixture();
  await h.client.refresh();
  await h.client.mutate("/api/auth/feishu/finalize", { confirm: true });
  const r = h.calls[1];
  assert.equal(r.options.credentials, "same-origin");
  assert.equal(r.options.cache, "no-store");
  assert.equal(r.options.headers["X-Feishu-CSRF"], "token");
  assert.equal(r.options.headers.Origin, undefined);
  h.setStatus({ enabled: false });
  await h.client.refresh();
  await assert.rejects(
    h.client.mutate("/api/auth/feishu/finalize", { confirm: true }),
  );
  assert.equal(h.calls.length, 3);
});
