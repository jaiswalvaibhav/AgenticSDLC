import { useEffect, useState, type MouseEvent, type ReactNode } from "react";

// Six pages, no router library: the path is the state. Links keep the query string
// (?profile=…), which says which board (local state or S3) this is.
export type Route = { page: "backlog" } | { page: "board" } | { page: "epics" } | { page: "issues" } | { page: "issue"; key: string };

export function parseRoute(path: string): Route {
  const issue = path.match(/^\/jira\/browse\/([^/]+)/);
  if (issue) return { page: "issue", key: decodeURIComponent(issue[1]) };
  if (path.startsWith("/jira/epics")) return { page: "epics" };
  if (path.startsWith("/jira/issues")) return { page: "issues" };
  return path.startsWith("/jira/board") ? { page: "board" } : { page: "backlog" };
}

const CHANGED = "mockjira:navigate";

export function navigate(to: string, replace = false) {
  const url = `${to}${window.location.search}`;
  if (replace) window.history.replaceState(null, "", url);
  else window.history.pushState(null, "", url);
  window.dispatchEvent(new Event(CHANGED));
}

export function useRoute(): Route {
  const [path, setPath] = useState(window.location.pathname);
  useEffect(() => {
    const update = () => setPath(window.location.pathname);
    window.addEventListener("popstate", update);
    window.addEventListener(CHANGED, update);
    return () => {
      window.removeEventListener("popstate", update);
      window.removeEventListener(CHANGED, update);
    };
  }, []);
  return parseRoute(path);
}

export function Link({ to, className, children }: { to: string; className?: string; children: ReactNode }) {
  const go = (e: MouseEvent) => {
    if (e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return; // let "open in new tab" work
    e.preventDefault();
    navigate(to);
  };
  return (
    <a href={`${to}${window.location.search}`} className={className} onClick={go}>
      {children}
    </a>
  );
}
