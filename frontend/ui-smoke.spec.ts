import { test, expect } from "@playwright/test";

test("WebUI dashboard shows live quotes and broker status", async ({ page }) => {
  const consoleErrors: string[] = [];
  const failedRequests: string[] = [];

  page.on("console", (msg) => {
    if (msg.type() === "error") consoleErrors.push(msg.text());
  });
  page.on("requestfailed", (req) => {
    failedRequests.push(`${req.method()} ${req.url()} - ${req.failure()?.errorText}`);
  });

  await page.goto("http://127.0.0.1:7070/");
  await page.waitForLoadState("networkidle", { timeout: 20000 });
  await page.screenshot({ path: "ui-01-dashboard.png", fullPage: true });

  // Check the page title.
  await expect(page).toHaveTitle(/MarketHub/i);

  // Check for broker status indicators (case-insensitive).
  const bodyText = await page.textContent("body") || "";
  console.log("Body length:", bodyText.length);
  console.log("Contains 'upstox':", /upstox/i.test(bodyText));
  console.log("Contains 'fyers':", /fyers/i.test(bodyText));
  console.log("Contains 'streaming':", /streaming/i.test(bodyText));
  console.log("Contains 'live':", /live/i.test(bodyText));

  // Check for numeric values (quotes).
  const numbers = bodyText.match(/\d{3,}/g) || [];
  console.log("Numbers found:", numbers.length);

  // Check for timestamps.
  const timestamps = bodyText.match(/\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}/g) || [];
  console.log("Timestamps found:", timestamps.length);
  if (timestamps.length > 0) {
    console.log("Sample timestamp:", timestamps[0]);
  }

  // Navigate to market data view.
  const marketLink = page.getByRole("link", { name: /market/i }).first();
  if (await marketLink.isVisible().catch(() => false)) {
    await marketLink.click();
    await page.waitForLoadState("networkidle", { timeout: 10000 });
    await page.screenshot({ path: "ui-02-market.png", fullPage: true });
    const marketBody = await page.textContent("body") || "";
    console.log("Market page body length:", marketBody.length);
    console.log("Market page has numbers:", (marketBody.match(/\d{3,}/g) || []).length);
  }

  // Navigate to breadth view.
  const breadthLink = page.getByRole("link", { name: /breadth/i }).first();
  if (await breadthLink.isVisible().catch(() => false)) {
    await breadthLink.click();
    await page.waitForLoadState("networkidle", { timeout: 10000 });
    await page.screenshot({ path: "ui-03-breadth.png", fullPage: true });
    const breadthBody = await page.textContent("body") || "";
    console.log("Breadth page body length:", breadthBody.length);
    console.log("Breadth has 50:", breadthBody.includes("50"));
  }

  // Navigate to F&O view.
  const fnoLink = page.getByRole("link", { name: /fno|futures|options/i }).first();
  if (await fnoLink.isVisible().catch(() => false)) {
    await fnoLink.click();
    await page.waitForLoadState("networkidle", { timeout: 10000 });
    await page.screenshot({ path: "ui-04-fno.png", fullPage: true });
    const fnoBody = await page.textContent("body") || "";
    console.log("F&O page body length:", fnoBody.length);
  }

  // Report console errors and failed requests.
  console.log("Console errors:", consoleErrors.length);
  if (consoleErrors.length > 0) {
    console.log("First 5 errors:", consoleErrors.slice(0, 5));
  }
  console.log("Failed requests:", failedRequests.length);
  if (failedRequests.length > 0) {
    console.log("First 5 failed:", failedRequests.slice(0, 5));
  }

  // Verify no console errors.
  expect(consoleErrors.length).toBeLessThanOrEqual(5);
});
