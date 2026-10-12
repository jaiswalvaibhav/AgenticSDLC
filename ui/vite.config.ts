import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The backend (make api) listens on 127.0.0.1:8000; proxying /api keeps the browser
// same-origin, so the backend needs no CORS setup.
export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": "http://127.0.0.1:8000" } },
});
