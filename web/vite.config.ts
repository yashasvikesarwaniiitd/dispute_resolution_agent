import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Served by the dispute service at /app (src/dispute_agent/app.py, _serve_web). In development
// `npm run dev` proxies the API to the service on loopback (`make run-api`).
export default defineConfig({
  base: "/app/",
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    proxy: { "/api": "http://127.0.0.1:8000" },
  },
});
