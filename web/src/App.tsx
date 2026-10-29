import { useEffect, useState } from "react";
import BudgetBadge from "./components/BudgetBadge";
import Replay from "./pages/Replay";
import Report from "./pages/Report";

const JUDGE_FIXTURE_NOTE =
  "Judge demo: a recorded-style investigation of a simulated Ford with a vacuum leak. Labelled sim — a second coil demo and real Ford bundles load the same way.";

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
  const [page, setPage] = useState<"replay" | "report">(judge ? "replay" : "replay");
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
      <h1>Master Mechanic Copilot</h1>
      <BudgetBadge />
      {judge ? <p>{JUDGE_FIXTURE_NOTE}</p> : null}
      <nav>
        <button onClick={() => setPage("replay")}>Replay</button>
        <button onClick={() => setPage("report")}>Report</button>
        <button onClick={toggleTheme} aria-label="Toggle colour theme">
          Theme: {theme ?? "system"}
        </button>
      </nav>
      {page === "replay" ? (
        <Replay
          autoJudge={judge}
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
