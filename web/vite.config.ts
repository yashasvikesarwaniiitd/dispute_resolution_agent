import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Served at /desk/ beside the API at /api/ws: by web_app.py locally (`make run-web`) and by
// streamlit_app.py on Streamlit Cloud, where everything sits under a /~/+/ prefix. So every
// path is relative. In development `npm run dev` proxies the API to `make run-web`.
export default defineConfig({
  base: "./",
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    proxy: { "/api": "http://127.0.0.1:8000" },
  },
});
