import { defineConfig, devices } from "@playwright/test";
export default defineConfig({
  testDir: "./tests",
  fullyParallel: true,
  workers: 2,
  retries: 0,
  reporter: "list",
  timeout: 30_000,
  use: {
    baseURL: "http://127.0.0.1:5173",
    trace: "off",
    screenshot: "off",
    video: "off",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: [
    {
      command: "npm run dev",
      url: "http://127.0.0.1:5173",
      reuseExistingServer: false,
    },
    ...(process.env.CQA_TEST_BACKEND === "1"
      ? [
          {
            command:
              "uv run --project ../backend --locked python tests/backend_harness.py",
            url: "http://127.0.0.1:8000/health",
            reuseExistingServer: false,
          },
        ]
      : []),
  ],
});
