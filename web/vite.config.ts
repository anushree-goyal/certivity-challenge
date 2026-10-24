import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In development the API runs separately (certivity serve); production builds are served by FastAPI.
export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy: { "/api": "http://127.0.0.1:8000" } },
});
