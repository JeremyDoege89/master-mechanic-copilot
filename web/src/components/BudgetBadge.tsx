import { useEffect, useState } from "react";
import { API_BASE } from "../api";

interface Budget {
  meta: { spend_usd: number; hard_stop_usd: number; remaining_usd: number };
  baseten: { spend_usd: number; hard_stop_usd: number; remaining_usd: number };
  public_mode?: boolean;
  public_live?: { spend_usd: number; cap_usd: number } | null;
}

/** Spend badge (M6 §6): Meta + Baseten ledgers, amber ≥80%, red at stop. */
export default function BudgetBadge() {
  const [budget, setBudget] = useState<Budget | null>(null);

  useEffect(() => {
    let live = true;
    fetch(`${API_BASE}/budget`)
      .then((r) => (r.ok ? r.json() : null))
      .then((b) => {
        if (live && b) setBudget(b as Budget);
      })
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, []);

  if (!budget) return null;
  const fmt = (n: number) => `$${n.toFixed(2)}`;
  const frac = (spend: number, stop: number) => (stop > 0 ? spend / stop : 0);
  const color = (spend: number, stop: number) => {
    const f = frac(spend, stop);
    if (f >= 1) return "#b00020";
    if (f >= 0.8) return "#8a5100";
    return "inherit";
  };
  return (
    <p aria-label="API spend">
      <span style={{ color: color(budget.meta.spend_usd, budget.meta.hard_stop_usd) }}>
        Meta {fmt(budget.meta.spend_usd)}/{fmt(budget.meta.hard_stop_usd)}
      </span>
      {" · "}
      <span style={{ color: color(budget.baseten.spend_usd, budget.baseten.hard_stop_usd) }}>
        Baseten {fmt(budget.baseten.spend_usd)}/{fmt(budget.baseten.hard_stop_usd)}
      </span>
      {budget.public_live && budget.public_live.cap_usd > 0 ? (
        <span>
          {" · "}
          <span style={{ color: color(budget.public_live.spend_usd, budget.public_live.cap_usd) }}>
            Public demo {fmt(budget.public_live.spend_usd)}/{fmt(budget.public_live.cap_usd)}
          </span>
        </span>
      ) : null}
    </p>
  );
}
