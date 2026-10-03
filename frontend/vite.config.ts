import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// En desarrollo, /api va a la API local. En produccion lo enruta el borde.
export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": "http://localhost:8000" } },
  preview: { proxy: { "/api": process.env.ZENIT_API ?? "http://localhost:8000" } },
});
