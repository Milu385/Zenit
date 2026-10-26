import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
// Poppins, la tipografia de los wireframes, servida desde la propia interfaz
// (sin pedirla a un tercero); solo el subconjunto latino, que trae las tildes
import "@fontsource/poppins/latin-400.css";
import "@fontsource/poppins/latin-500.css";
import "@fontsource/poppins/latin-600.css";
import "@fontsource/poppins/latin-700.css";
import "uplot/dist/uPlot.min.css";
import "./estilos.css";
import "./marco.css";
import App from "./App";

createRoot(document.getElementById("raiz")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
