import { createLogger, defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const logger = createLogger();
const error = logger.error.bind(logger);
logger.error = (message, options) => {
  if (message.includes("http proxy error:")) {
    error("cqa.proxy event=api_proxy_failed error=BACKEND_UNAVAILABLE", {
      timestamp: options?.timestamp,
    });
  } else {
    error(message, options);
  }
};

export default defineConfig({
  customLogger: logger,
  plugins: [react()],
  server: {
    proxy: { "/api": { target: "http://127.0.0.1:8000", changeOrigin: true } },
  },
});
