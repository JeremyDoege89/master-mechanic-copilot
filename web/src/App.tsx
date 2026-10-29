import { useEffect, useState } from "react";
import BudgetBadge from "./components/BudgetBadge";
import Replay from "./pages/Replay";
import Report from "./pages/Report";

function themeInit(): string | null {
  try {
    return window.localStorage.getItem("mmc-theme");
  } catch {
    return null;
  }
}

export default function App() {
  const params = new URLSearchParams(window.location.search);
  const judge = params.get("judge") === "1";
  const [page, setPage] = useState<"replay" | "report">("replay");
  const [reportId, setReportId] = useState<string | undefined>(undefined);
  const [theme, setTheme] = useState<string | null>(null);

  useEffect(() => {
    const saved = themeInit();
    if (saved === "light" || saved === "dark") {
      document.documentElement.dataset.theme = saved;
      setTheme(saved);
    }
  }, []);

  const toggleTheme = () => {
    const next = (theme ?? (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light")) === "dark"
      ? "light"
      : "dark";
    document.documentElement.dataset.theme = next;
    try {
      window.localStorage.setItem("mmc-theme", next);
    } catch {
      /* storage unavailable: theme still applies for this visit */
    }
    setTheme(next);
  };

  return (
    <main>
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">MMC</span>
          <span className="brand-text">
            <strong>Master Mechanic Copilot</strong>
            <small>{judge ? "Judge demo · simulated Ford, real AI outputs" : "Vehicle diagnosis copilot"}</small>
          </span>
        </div>
        <div className="topbar-right">
          <BudgetBadge />
          <nav aria-label="Pages">
            <button
              onClick={() => setPage("replay")}
              aria-current={page === "replay"}
              disabled={page === "replay"}
            >
              Replay
            </button>
            <button
              onClick={() => setPage("report")}
              aria-current={page === "report"}
              disabled={page === "report" || !reportId}
              title={reportId ? `Open report ${reportId.slice(0, 8)}` : "Load a session first"}
            >
              Report{reportId ? "" : ""}
            </button>
            <button onClick={toggleTheme} aria-label="Toggle colour theme">
              Theme: {theme ?? "system"}
            </button>
          </nav>
        </div>
      </header>
      {page === "replay" ? (
        <Replay
          autoJudge={judge}
          onLatestReport={(id) => setReportId(id ?? undefined)}
          onReport={(id) => {
            setReportId(id);
            setPage("report");
          }}
        />
      ) : (
        <Report reportId={reportId} />
      )}
    </main>
  );
}
