import { useState } from "react";
import BudgetBadge from "./components/BudgetBadge";
import Replay from "./pages/Replay";
import Report from "./pages/Report";

const JUDGE_FIXTURE_NOTE =
  "Judge demo: a simulated healthy session bundle. Labelled sim — swap in a real Ford bundle by file drop.";

export default function App() {
  const params = new URLSearchParams(window.location.search);
  const judge = params.get("judge") === "1";
  const [page, setPage] = useState<"replay" | "report">(judge ? "replay" : "replay");
  const [reportId, setReportId] = useState<string | undefined>(undefined);

  return (
    <main>
      <h1>Master Mechanic Copilot</h1>
      <BudgetBadge />
      {judge ? <p>{JUDGE_FIXTURE_NOTE}</p> : null}
      <nav>
        <button onClick={() => setPage("replay")}>Replay</button>
        <button onClick={() => setPage("report")}>Report</button>
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
