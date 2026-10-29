import { createContext } from "react";

// Lugar del marco, a la derecha de todo, donde cada pantalla pone su columna
// azul (ColumnaDerecha). Asi la columna va de arriba a abajo de la ventana y
// se queda quieta al bajar, igual que la barra negra de la izquierda.
// Es null hasta que el marco se monta.
export const RanuraDerecha = createContext<HTMLElement | null>(null);
