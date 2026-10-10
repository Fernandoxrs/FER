const { chromium } = require("playwright");
const path = require("path");

const [, , query = "", out = "out.png", size = "1024"] = process.argv;

(async () => {
  const browser = await chromium.launch({ executablePath: "/opt/pw-browsers/chromium", args: ["--allow-file-access-from-files"] });
  const page = await browser.newPage({ viewport: { width: 1024, height: 1024 }, deviceScaleFactor: +size / 1024 });
  const url = "file://" + path.join(__dirname, process.env.PAGE || "logo.html") + (query ? "?" + query : "");
  await page.goto(url);
  await page.waitForSelector("body[data-ready='1']", { timeout: 15000 });
  await page.waitForTimeout(300);
  await page.locator("svg").screenshot({ path: out, omitBackground: true });
  await browser.close();
})();
