import { defineConfig } from "vite";
export default defineConfig({
  root: "desktop/renderer",
  base: "./",
  build: { outDir: "../../ui-dist", emptyOutDir: true },
});
