import { defineConfig, devices } from "@playwright/test";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { mkdtempSync } from "node:fs";

const python = process.env.HELIOS_E2E_PYTHON || "../.venv/bin/python";
const generatedDirectory = process.env.HELIOS_E2E_DB_PATH ? "" : mkdtempSync(join(tmpdir(), "helios-e2e-"));
const databasePath = process.env.HELIOS_E2E_DB_PATH || join(generatedDirectory, "helios.sqlite3");
process.env.HELIOS_E2E_DB_PATH = databasePath;
if (generatedDirectory) process.env.HELIOS_E2E_TEMP_DIR = generatedDirectory;

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? "github" : "list",
  globalTeardown: "./e2e/global-teardown.ts",
  use: {
    baseURL: "http://127.0.0.1:5059",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"] } },
    { name: "mobile-chromium", use: { ...devices["Pixel 7"] } },
  ],
  webServer: {
    command: `HELIOS_AUTH=0 HELIOS_LOAD_DOTENV=0 HELIOS_DB_ENCRYPTION=auto HELIOS_DB_PATH=${JSON.stringify(databasePath)} HELIOS_AUTO_LIVE_SYMBOLS=off HELIOS_INSTITUTIONAL_CONTROLS=0 HELIOS_HOST=127.0.0.1 HELIOS_PORT=5059 ${python} ../serve.py`,
    url: "http://127.0.0.1:5059/",
    timeout: 120_000,
    reuseExistingServer: false,
  },
});
