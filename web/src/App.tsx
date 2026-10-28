import { useState } from "react";
import Replay from "./pages/Replay";
import Report from "./pages/Report";

export default function App() {
  const [page, setPage] = useState<"replay" | "report">("replay");
  return (
    <main>
      <h1>Master Mechanic Copilot</h1>
      <nav>
        <button onClick={() => setPage("replay")}>Replay</button>
        <button onClick={() => setPage("report")}>Report</button>
      </nav>
      {page === "replay" ? <Replay /> : <Report />}
    </main>
  );
}
