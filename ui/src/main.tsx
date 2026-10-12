import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import MockJiraApp from "./jira/MockJiraApp";
import "./styles.css";

// /jira/* is the mock Jira (opened in its own tab from the workflow page); everything else is the workflow page.
const Root = window.location.pathname.startsWith("/jira") ? MockJiraApp : App;

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <Root />
  </StrictMode>,
);
