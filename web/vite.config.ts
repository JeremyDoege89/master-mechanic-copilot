import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/sessions": "http://localhost:8000",
      "/reports": "http://localhost:8000",
      "/replay": "http://localhost:8000",
      "/budget": "http://localhost:8000",
      "/health": "http://localhost:8000",
      "/meta": "http://localhost:8000",
    },
  },
});
