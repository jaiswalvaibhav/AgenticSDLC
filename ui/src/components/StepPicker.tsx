import type { Step } from "../types";

interface Props {
  steps: Step[];
  targets: string[];
  provideIds: Set<string>;
  onToggle: (stepId: string) => void;
}

// Tick = "generate this artifact". Its direct inputs show up as "provide" automatically
// (from /api/preview); ticking a provide step turns it into a generate step.
export function StepPicker({ steps, targets, provideIds, onToggle }: Props) {
  const names = new Map(steps.map((s) => [s.id, s.artifact]));
  return (
    <ul className="steps">
      {steps.map((step) => {
        const ticked = targets.includes(step.id);
        return (
          <li key={step.id} className={ticked ? "step ticked" : "step"}>
            <label>
              <input type="checkbox" checked={ticked} onChange={() => onToggle(step.id)} />
              <span className="artifact">{step.artifact}</span>
            </label>
            {step.automation && <span className="chip automated">automated</span>}
            {!ticked && provideIds.has(step.id) && <span className="chip provide">provide</span>}
            <span className="meta">
              {step.owner}
              {step.inputs.length > 0 && ` · needs: ${step.inputs.map((i) => names.get(i) ?? i).join(", ")}`}
            </span>
          </li>
        );
      })}
    </ul>
  );
}
