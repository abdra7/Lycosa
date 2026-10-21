import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import "./styles/app.css";
import { Root } from "./App";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <Root />
  </StrictMode>,
);
