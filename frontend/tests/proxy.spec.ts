import { once } from "node:events";
import { createServer as createHttpServer } from "node:http";
import { test, expect } from "@playwright/test";
import { createServer, type ViteDevServer } from "vite";
import config from "../vite.config";

test("dev proxy reports backend failure without logging the request or exception", async () => {
  const backend = createHttpServer((request) => request.socket.destroy());
  backend.listen(0, "127.0.0.1");
  await once(backend, "listening");
  const address = backend.address();
  if (!address || typeof address === "string") throw new Error("Expected TCP");

  const output: string[] = [];
  const original = console.error;
  let dev: ViteDevServer | undefined;
  console.error = (...values: unknown[]) => output.push(values.join(" "));
  try {
    dev = await createServer({
      configFile: false,
      customLogger: config.customLogger,
      optimizeDeps: { noDiscovery: true },
      server: {
        host: "127.0.0.1",
        port: 0,
        hmr: false,
        proxy: { "/api": { target: `http://127.0.0.1:${address.port}` } },
      },
    });
    await dev.listen();
    const local = dev.httpServer?.address();
    if (!local || typeof local === "string") throw new Error("Expected TCP");
    const marker = "SYNTHETIC_PROXY_PRIVATE";
    const response = await fetch(
      `http://127.0.0.1:${local.port}/api/v1/conversations?group_id=${marker}`,
    );
    await response.text();
    expect(response.status).toBe(500);
    expect(output).toHaveLength(1);
    expect(output[0]).toContain(
      "event=api_proxy_failed error=BACKEND_UNAVAILABLE",
    );
    expect(output[0]).not.toContain(marker);
    expect(output[0]).not.toContain("socket hang up");
  } finally {
    console.error = original;
    await dev?.close();
    await new Promise<void>((resolve) => backend.close(() => resolve()));
  }
});
