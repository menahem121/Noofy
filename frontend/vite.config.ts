import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import { readFileSync } from "fs";

declare const process: {
  env: Record<string, string | undefined>;
};

const { version } = JSON.parse(readFileSync("./package.json", "utf-8")) as { version: string };

const tauriDevHost = process.env.TAURI_DEV_HOST;
const tauriPlatform = process.env.TAURI_ENV_PLATFORM;
const tauriDebug = process.env.TAURI_ENV_DEBUG;
const devBackendPort = process.env.VITE_DEV_BACKEND_PORT ?? "8765";

export default defineConfig({
  clearScreen: false,
  plugins: [react()],
  define: { __APP_VERSION__: JSON.stringify(version) },
  envPrefix: ["VITE_", "TAURI_ENV_*"],
  server: {
    host: tauriDevHost || "127.0.0.1",
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": {
        target: `http://127.0.0.1:${devBackendPort}`,
        changeOrigin: true,
      },
    },
  },
  preview: {
    host: "127.0.0.1",
    port: 4173,
    strictPort: true,
    proxy: {
      "/api": {
        target: `http://127.0.0.1:${devBackendPort}`,
        changeOrigin: true,
      },
    },
  },
  build: {
    target: tauriPlatform === "windows" ? "chrome105" : "safari13",
    minify: !tauriDebug,
    sourcemap: Boolean(tauriDebug),
  },
  test: {
    environment: "jsdom",
    setupFiles: "./src/test/setup.ts",
    globals: true,
  },
});
