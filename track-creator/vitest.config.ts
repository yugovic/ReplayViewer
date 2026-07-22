import { defineConfig } from "vitest/config";

// Separate from vite.config.ts (whose root points at preview/ for the viewer).
export default defineConfig({
  test: { include: ["src/**/*.test.ts"] },
});
