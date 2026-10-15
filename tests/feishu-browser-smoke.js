/* SYNTHETIC browser smoke: every API and navigation is mocked. No real credentials or sends.
 * NODE_PATH=<scratch>/node_modules node tests/feishu-browser-smoke.js
 */
const { chromium } = require("playwright");
const fs = require("node:fs/promises"),
  path = require("node:path"),
  assert = require("node:assert/strict");
(async () => {
  const browser = await chromium.launch({ channel: "chrome", headless: true });
  try {
    const page = await browser.newPage();
    let enabled = true,
      authenticated = false,
      bound = false,
      pending = null,
      workspace = "A",
      subscribed = false;
    const calls = [],
      errors = [];
    page.on("pageerror", (e) => errors.push(e.message));
    await page.route("**/*", async (route) => {
      const req = route.request(),
        u = new URL(req.url());
      if (u.origin !== "https://synthetic-ticket.test") return route.abort();
      const url = u.pathname;
      let data = { ok: true };
      calls.push({
        url,
        method: req.method(),
        body: req.postDataJSON(),
        headers: await req.allHeaders(),
      });
      if (url.startsWith("/static/")) {
        try {
          const body = await fs.readFile(path.join(__dirname, "..", url));
          return route.fulfill({
            body,
            contentType: url.endsWith(".js")
              ? "application/javascript"
              : url.endsWith(".css")
                ? "text/css"
                : "image/png",
          });
        } catch (_) {
          return route.fulfill({ status: 404, body: "" });
        }
      }
      if (url === "/" || url === "/login")
        return route.fulfill({
          contentType: "text/html",
          body: await fs.readFile(
            path.join(
              __dirname,
              "../static",
              url === "/login" ? "login.html" : "index.html",
            ),
          ),
        });
      if (url === "/api/auth/feishu/status")
        data = {
          ok: true,
          enabled,
          authenticated,
          bound,
          binding: bound ? { name: "Bound" } : null,
          pending_intent: pending,
          pending_binding:
            pending === "bind"
              ? { name: "<img src=x onerror=alert(1)>" }
              : null,
          csrf_token: "synthetic-token",
        };
      else if (url === "/api/auth/login") {
        authenticated = true;
      } else if (url === "/api/auth/feishu/finalize") {
        if (pending === "bind") bound = true;
        else authenticated = true;
        pending = null;
      } else if (url === "/api/session")
        data = {
          user: {
            workspace_id: workspace,
            display_name: "Synthetic Member",
            is_admin: false,
          },
          workspaces: [
            { id: "A", name: "A" },
            { id: "B", name: "B" },
          ],
        };
      else if (url === "/api/workspaces/switch") {
        workspace = req.postDataJSON().workspace_id;
      } else if (url === "/api/notifications/feishu/open-ticket") {
        workspace = "B";
        data = { ok: true, workspace_id: "B", ticket_id: "TKT-42" };
      } else if (url === "/api/notifications/feishu/subscriptions")
        data = {
          items: [
            {
              mailbox_id: "m",
              name: "Synthetic inbox",
              workspace_id: workspace,
              enabled: subscribed ? 1 : 0,
            },
          ],
        };
      else if (url === "/api/notifications/feishu/subscriptions/m") {
        subscribed = req.postDataJSON().enabled;
        data = { ok: true, enabled: subscribed, mailbox_id: "m" };
      } else if (url === "/api/tickets")
        data = {
          tickets: [],
          mailboxes: [],
          category_options: [],
          counts: {},
          tag_options: [],
          category_counts: {},
        };
      else if (url === "/api/tickets/TKT-42")
        data = {
          ticket: {
            id: "TKT-42",
            subject: "Synthetic deep link",
            status: "open",
            priority: "normal",
          },
          messages: [],
          reply_recipients: {
            parent_message_id: null,
            reply: { to: [], cc: [] },
            reply_all: { to: [], cc: [] },
          },
        };
      else if (url === "/api/mail/inbound-cursor")
        data = { cursor: 0, messages: [] };
      return route.fulfill({
        contentType: "application/json",
        body: JSON.stringify(data),
      });
    });
    await page.goto("https://synthetic-ticket.test/login?ticket=TKT-42");
    await page.waitForFunction(
      () => !document.querySelector("#feishuLogin").disabled,
    );
    assert.equal(await page.locator("#loginButton").isEnabled(), true);
    // Synthetic fixture password, not a real credential.
    await page.locator("#username").fill("synthetic");
    await page.locator("#password").fill("synthetic-not-a-credential");
    await page.locator("#loginButton").click();
    await page.waitForFunction(
      () =>
        document.querySelector("#detail h2")?.textContent ===
        "Synthetic deep link",
    );
    const authorized = calls.findIndex((x) => x.url.endsWith("/open-ticket"));
    const reconciled = calls.findIndex(
      (x, i) => i > authorized && x.url === "/api/session",
    );
    const detail = calls.findIndex((x) => x.url === "/api/tickets/TKT-42");
    assert.ok(
      authorized >= 0 && reconciled > authorized && detail > reconciled,
    );
    assert.equal(
      calls[authorized].headers.origin,
      "https://synthetic-ticket.test",
    );
    assert.equal(
      await page.evaluate(() => sessionStorage.getItem("ticket.pending-link")),
      null,
    );
    for (const width of [1280, 390]) {
      await page.setViewportSize({ width, height: 844 });
      if (width < 850) await page.locator("#mobileMenu").click();
      await page.locator("#feishuSettings").click();
      await page.waitForFunction(
        () => document.querySelector("#feishuBind").disabled === false,
      );
      assert.equal(await page.locator("#feishuDialog").isVisible(), true);
      assert.ok(
        await page.evaluate(
          () =>
            document.querySelector("#feishuDialog").getBoundingClientRect()
              .width <= innerWidth,
        ),
      );
      await page.locator("#feishuClose").click();
    }
    pending = "bind";
    await page.goto("https://synthetic-ticket.test/?feishu=confirm");
    await page.waitForFunction(
      () => document.querySelector("#feishuFinalize").disabled === false,
    );
    assert.equal(await page.locator("#feishuName img").count(), 0);
    assert.match(await page.locator("#feishuName").textContent(), /<img/);
    await page.locator("#feishuConfirm").check();
    await page.locator("#feishuFinalize").click();
    await page.waitForFunction(
      () =>
        document.querySelector("#feishuSubscriptions input")?.disabled ===
        false,
    );
    assert.equal(
      await page.locator("#feishuSubscriptions input").isChecked(),
      false,
    );
    await page.locator("#feishuSubscriptions input").check();
    await page.waitForFunction(
      () =>
        document.querySelector("#feishuSubscriptions input")?.checked === true,
    );
    assert.equal(subscribed, true);
    enabled = false;
    await page.goto("https://synthetic-ticket.test/login");
    await page.waitForTimeout(100);
    assert.equal(await page.locator("#feishuLogin").isVisible(), false);
    assert.equal(await page.locator("#loginButton").isEnabled(), true);
    enabled = true;
    authenticated = false;
    pending = "login";
    await page.goto(
      "https://synthetic-ticket.test/login?feishu=complete&ticket=TKT-42",
    );
    await page.waitForFunction(
      () =>
        document.querySelector("#detail h2")?.textContent ===
        "Synthetic deep link",
    );
    assert.equal(authenticated, true);
    assert.deepEqual(errors, []);
    console.log(
      "SYNTHETIC MOCKED API SMOKE PASS: password/OAuth deep links, authorization/session/detail order, browser Origin, member desktop/mobile dialog, escaped bind identity, explicit subscriptions, disabled fallback; no real Feishu/email sends.",
    );
  } finally {
    await browser.close();
  }
})().catch((e) => {
  console.error(e);
  process.exitCode = 1;
});
