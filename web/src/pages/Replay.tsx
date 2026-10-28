import HypothesisChart from "../components/HypothesisChart";
import SamOverlay from "../components/SamOverlay";

/** M0 shell: session replay binds to a real bundle in M6. */
export default function Replay() {
  return (
    <section>
      <h2>Replay</h2>
      <p>No session loaded (M0 shell).</p>
      <HypothesisChart />
      <SamOverlay />
    </section>
  );
}
