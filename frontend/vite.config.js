import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// During development (npm run dev) API calls go to the Python server on port 8000.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: { "/api": "http://localhost:8000" },
    // The project is inside OneDrive: file-change events can be missed there, so check files by polling.
    watch: { usePolling: true, interval: 500 },
  },
  build: { outDir: "dist", emptyOutDir: true },
});
