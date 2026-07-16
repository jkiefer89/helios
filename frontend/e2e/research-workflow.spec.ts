import { expect, test, type Page } from "@playwright/test";

function priceCsv(rows = 340) {
  const end = new Date();
  end.setUTCHours(0, 0, 0, 0);
  while (end.getUTCDay() === 0 || end.getUTCDay() === 6) end.setUTCDate(end.getUTCDate() - 1);
  const dates: Date[] = [];
  const cursor = new Date(end);
  while (dates.length < rows) {
    if (cursor.getUTCDay() !== 0 && cursor.getUTCDay() !== 6) dates.push(new Date(cursor));
    cursor.setUTCDate(cursor.getUTCDate() - 1);
  }
  dates.reverse();
  const lines = ["Date,Close,Volume"];
  let price = 100;
  dates.forEach((date, index) => {
    price *= 1 + (index % 11 === 0 ? -0.002 : 0.0009);
    lines.push(`${date.toISOString().slice(0, 10)},${price.toFixed(4)},${1_000_000 + index * 100}`);
  });
  return lines.join("\n");
}

async function openDataIntake(page: Page) {
  const heading = page.getByRole("heading", { name: /Real data setup|Real data active/i });
  if (await heading.isVisible()) return;
  const rail = page.getByRole("button", { name: "Show data intake panel" });
  if (await rail.isVisible()) {
    await rail.click();
  } else {
    await page.getByRole("button", { name: /Fetch or upload benchmark data|Resolve .*real histor/i }).first().click();
  }
  await expect(heading).toBeVisible();
}

test("Command Center recovers after a transient API failure", async ({ page, isMobile }) => {
  test.skip(Boolean(isMobile), "desktop recovery path");
  let attempts = 0;
  await page.route("**/api/command-center", async (route) => {
    attempts += 1;
    if (attempts === 1) {
      await route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ error: "temporary failure" }) });
      return;
    }
    await route.continue();
  });

  await page.goto("/");
  await expect(page.getByText("Command Center unavailable", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Retry", exact: true }).click();
  await expect(page.getByText("Command Center unavailable", { exact: true })).not.toBeVisible();
  await expect(page.getByText("temporary failure", { exact: false })).not.toBeVisible();
  expect(attempts).toBeGreaterThanOrEqual(2);
});

test("first run remains blocked until real evidence is imported", async ({ page, isMobile }) => {
  test.skip(Boolean(isMobile), "the mutation journey runs once on desktop; mobile has a dedicated layout test");
  await page.goto("/");
  await expect(page.getByRole("button", { name: "Open Command Center" })).toBeVisible();
  await expect(page.getByText(/No data|Blocked/, { exact: true }).first()).toBeVisible();

  await openDataIntake(page);
  await page.getByLabel("Price CSV").setInputFiles({
    name: "e2e-real.csv",
    mimeType: "text/csv",
    buffer: Buffer.from(priceCsv()),
  });
  await page.getByLabel("Uploaded series symbol").fill("E2EREAL");
  await page.getByRole("button", { name: "Upload price history" }).click();

  await expect(page.getByRole("heading", { name: "Research data workspace" })).toBeVisible();
  await expect(page.getByText("E2EREAL", { exact: true }).first()).toBeVisible();
  await page.getByRole("tab", { name: /Data Quality/ }).click();
  await expect(page.getByRole("heading", { name: "Research readiness dashboard" })).toBeVisible();

  await openDataIntake(page);
  await page.getByLabel("Model file").setInputFiles({
    name: "e2e-model.csv",
    mimeType: "text/csv",
    buffer: Buffer.from("Ticker,Weight\nE2EREAL,50\nE2EMISSING,50\n"),
  });
  await page.getByLabel("Model name").fill("E2E Real Model");
  await page.getByRole("button", { name: "Upload model" }).click();

  await expect(page.getByRole("heading", { name: "Client model workspace" })).toBeVisible();
  await expect(page.getByText("E2E Real Model", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("E2EMISSING", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("1/2", { exact: true }).first()).toBeVisible();
  await expect(page.getByRole("heading", { name: "Model Validation Dashboard" })).toBeVisible();

  await openDataIntake(page);
  await page.getByLabel("Price CSV").setInputFiles({
    name: "e2e-missing.csv",
    mimeType: "text/csv",
    buffer: Buffer.from(priceCsv()),
  });
  await page.getByLabel("Uploaded series symbol").fill("E2EMISSING");
  await page.getByRole("button", { name: "Upload price history" }).click();
  await expect(page.getByRole("heading", { name: "Research data workspace" })).toBeVisible();
  await page.getByRole("tab", { name: /Data Quality/ }).click();
  await expect(page.getByRole("heading", { name: "Research readiness dashboard" })).toBeVisible();

  await page.getByRole("tab", { name: /Models/ }).click();
  await expect(page.getByText("2/2", { exact: true }).first()).toBeVisible();

  await page.getByRole("button", { name: "Research", exact: true }).click();
  await page.getByRole("tab", { name: /Opportunity Radar/ }).click();
  await expect(page.getByRole("heading", { name: /Ranked real-data review queue/i })).toBeVisible();
  await expect(page.getByText("E2EREAL", { exact: true }).first()).toBeVisible();

  await page.getByRole("tab", { name: /Analysis/ }).click();
  await expect(page.getByRole("heading", { name: "Instrument and model detail" })).toBeVisible();
  await page.getByRole("combobox", { name: "Analysis target" }).click();
  await page.getByRole("option", { name: /E2E Real Model/ }).click();
  await page.getByRole("button", { name: "Analyze" }).click();
  await expect(page.getByText("E2E Real Model", { exact: true }).first()).toBeVisible();
  for (const horizon of ["6M", "1Y", "3Y", "5Y"]) {
    await page.getByRole("button", { name: horizon, exact: true }).click();
    await expect(page.getByRole("heading", { name: new RegExp(`${horizon} Strategic Value Projection`) })).toBeVisible();
  }
  const convictionPanel = page.getByRole("heading", { name: "How Conviction Can Improve" }).locator("xpath=ancestor::section[1]");
  await expect(convictionPanel.getByText("How Helios receives evidence", { exact: true })).toBeVisible();
  await expect(convictionPanel.getByText("Sources", { exact: true }).first()).toBeVisible();
  const convictionOverflow = await convictionPanel.evaluate((node) => ({
    clientWidth: node.clientWidth,
    scrollWidth: node.scrollWidth,
  }));
  expect(convictionOverflow.scrollWidth).toBeLessThanOrEqual(convictionOverflow.clientWidth + 1);
  await page.setViewportSize({ width: 390, height: 844 });
  const narrowOverflow = await page.evaluate(() => ({
    viewport: document.documentElement.clientWidth,
    body: document.body.scrollWidth,
    conviction: document.querySelector(".conviction-guidance")?.scrollWidth || 0,
    convictionWidth: document.querySelector(".conviction-guidance")?.clientWidth || 0,
  }));
  expect(narrowOverflow.body).toBeLessThanOrEqual(narrowOverflow.viewport + 1);
  expect(narrowOverflow.conviction).toBeLessThanOrEqual(narrowOverflow.convictionWidth + 1);
  await page.setViewportSize({ width: 1280, height: 720 });
  await page.getByRole("button", { name: "Record Helios signal" }).click();
  await expect(page.getByRole("button", { name: "Signal recorded" })).toBeVisible();

  await page.getByRole("tab", { name: /Strategy Lab/ }).click();
  await page.getByRole("button", { name: "Run", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Evidence Verdict" })).toBeVisible();

  await page.getByRole("button", { name: "Evidence & Risk", exact: true }).click();
  await page.getByRole("tab", { name: /Evidence Lab/ }).click();
  await page.getByRole("button", { name: "Run evidence" }).click();
  await expect(page.getByRole("heading", { name: "Alpha vs Benchmark" })).toBeVisible();

  await page.getByRole("tab", { name: /Risk Analytics/ }).click();
  await page.getByRole("button", { name: "Analyze risk" }).click();
  await expect(page.getByRole("heading", { name: "Concentration", exact: true })).toBeVisible();

  await page.getByRole("button", { name: "Decisions", exact: true }).click();
  await page.getByRole("tab", { name: /Decision Journal/ }).click();
  await page.getByLabel("Ticker", { exact: true }).fill("E2EREAL");
  await page.getByLabel("Decision rationale").fill("E2E workflow record from eligible uploaded history.");
  await page.getByRole("button", { name: "Record decision" }).click();
  const decisionsPanel = page.getByRole("heading", { name: "Decisions", exact: true }).locator("xpath=ancestor::section[1]");
  await expect(decisionsPanel.getByText("E2EREAL", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "Reports", exact: true }).click();
  await page.getByRole("combobox", { name: "Report target" }).click();
  await page.getByRole("option", { name: /E2E Real Model/ }).click();
  await page.getByRole("button", { name: "Build preview" }).click();
  await expect(page.getByRole("heading", { name: /E2E Real Model/ }).first()).toBeVisible();
  await expect(page.getByText("Helios Report Preview")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Data Quality / Persistence" })).toBeVisible();
  await page.getByRole("button", { name: "Save snapshot" }).click();
  await expect(page.getByRole("table", { name: /saved report snapshots/i })).toContainText("Research ready");
  await expect(page.getByRole("table", { name: /saved report snapshots/i })).toContainText("Advisor Review");
});

test("mobile puts the research surface before the collapsible intake", async ({ page, isMobile }) => {
  test.skip(!isMobile, "mobile ordering check");
  await page.goto("/");
  await page.getByRole("tab", { name: /Instruments/ }).click();
  const main = page.locator("#main-content");
  const rail = page.getByRole("button", { name: "Show data intake panel" });
  await expect(main).toBeVisible();
  await expect(rail).toBeVisible();
  const mainBox = await main.boundingBox();
  const railBox = await rail.boundingBox();
  expect(mainBox && railBox && mainBox.y < railBox.y).toBeTruthy();
});

const workspaceRoutes = [
  ["setup", "Research data workspace"],
  ["instruments", "Available price histories"],
  ["data-quality", "Research readiness dashboard"],
  ["models", "Client model workspace"],
  ["opportunities", "Ranked real-data review queue"],
  ["analysis", "Instrument and model detail"],
  ["strategy", "No-lookahead signal evidence"],
  ["evidence", "Walk-forward evidence"],
  ["clinic", "Model diagnostics and hypothetical improvements"],
  ["risk", "Risk + portfolio analytics"],
  ["journal", "Paper performance tracking"],
  ["decisions", "Your calls vs the engine — scored"],
  ["reports", "Institutional Report System"],
] as const;

test("Data Setup remains a full-width single-column workflow at tablet width", async ({ page, isMobile }) => {
  test.skip(Boolean(isMobile), "tablet-specific layout check");
  await page.setViewportSize({ width: 1024, height: 900 });
  await page.goto("/#/setup");
  await expect(page.getByRole("heading", { name: "Research data workspace" })).toBeVisible();
  const panel = page.locator(".data-intake-panel--full");
  await expect(panel).toBeVisible();
  const forms = panel.locator(".data-intake-grid form");
  await expect(forms).toHaveCount(3);
  const boxes = await forms.evaluateAll((nodes) => nodes.map((node) => {
    const box = node.getBoundingClientRect();
    return { x: box.x, y: box.y, width: box.width };
  }));
  expect(Math.max(...boxes.map((box) => box.x)) - Math.min(...boxes.map((box) => box.x))).toBeLessThan(2);
  expect(boxes[1].y).toBeGreaterThan(boxes[0].y);
  expect(boxes[2].y).toBeGreaterThan(boxes[1].y);
  expect(Math.min(...boxes.map((box) => box.width))).toBeGreaterThan(700);
  const overflow = await page.evaluate(() => ({
    viewport: document.documentElement.clientWidth,
    body: document.body.scrollWidth,
  }));
  expect(overflow.body).toBeLessThanOrEqual(overflow.viewport + 1);
});

test("global controls and every operator workspace remain reachable", async ({ page, isMobile }) => {
  test.skip(Boolean(isMobile), "desktop control and route coverage");
  await page.goto("/");
  await page.getByRole("button", { name: "Advisor Console" }).click();
  await expect(page.getByText("Identity", { exact: true })).toBeVisible();
  await expect(page.getByText("Roles", { exact: true })).toBeVisible();

  const search = page.getByRole("combobox", { name: "Search instruments, models, and reports" });
  await search.fill("Risk Analytics");
  await search.press("Enter");
  await expect(page.getByRole("heading", { name: "Risk + portfolio analytics" })).toBeVisible();

  for (const [route, heading] of workspaceRoutes) {
    await page.goto(`/#/${route}`);
    await expect(page.getByRole("heading", { name: heading, exact: true })).toBeVisible();
  }
});

test("mobile workspaces contain overflow inside their own controls", async ({ page, isMobile }) => {
  test.skip(!isMobile, "mobile route overflow coverage");
  for (const [route, heading] of workspaceRoutes) {
    await page.goto(`/#/${route}`);
    await expect(page.getByRole("heading", { name: heading, exact: true })).toBeVisible();
    const dimensions = await page.evaluate(() => ({
      viewport: document.documentElement.clientWidth,
      body: document.body.scrollWidth,
      document: document.documentElement.scrollWidth,
    }));
    expect(dimensions.body, `${route} body overflow`).toBeLessThanOrEqual(dimensions.viewport + 1);
    expect(dimensions.document, `${route} document overflow`).toBeLessThanOrEqual(dimensions.viewport + 1);
  }
});
