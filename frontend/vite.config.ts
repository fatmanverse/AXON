/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "src"),
    },
  },
  server: {
    port: 5173,
    // 后端契约走 /api 与 /ws;开发期代理到 FastAPI,避免 CORS 与硬编码域名
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
      "/ws": {
        target: "ws://localhost:8000",
        ws: true,
      },
    },
  },
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: ["./tests/setup.ts"],
    // 约束5:测试只在 tests/ 目录,禁止 src 内 *.test.*
    include: ["tests/**/*.{test,spec}.{ts,tsx}"],
    exclude: ["src/**/*.test.*", "node_modules/**"],
    coverage: {
      provider: "v8",
      reportsDirectory: "./coverage",
      include: ["src/**"],
    },
  },
});
