import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import servidorSimulado from "./simulado/servidor";

// En desarrollo, /api va a la API local; con `npm run dev:simulado` (modo
// "simulado") lo contesta simulado/servidor.ts. En produccion lo enruta el borde.
export default defineConfig(({ mode }) => ({
  plugins: mode === "simulado" ? [react(), servidorSimulado()] : [react()],
  server: mode === "simulado" ? {} : { proxy: { "/api": "http://localhost:8000" } },
  preview: { proxy: { "/api": process.env.ZENIT_API ?? "http://localhost:8000" } },
}));
