"""Loads config/workflow.yaml (or a use case's override) and computes which tickets
`workflow start` should create for a given --from/--steps selection."""
from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class Step:
    id: str
    owner: str
    inputs: tuple[str, ...]
    artifact: str
    automation: str | None
    optional: bool = False


class WorkflowError(ValueError):
    pass


class WorkflowRegistry:
    def __init__(self, steps: list[Step]):
        by_id = {}
        for s in steps:
            if s.id in by_id:
                raise WorkflowError(f"duplicate step id: {s.id}")
            by_id[s.id] = s
        for s in steps:
            for dep in s.inputs:
                if dep not in by_id:
                    raise WorkflowError(f"step {s.id!r} depends on unknown step {dep!r}")
        self._by_id = by_id
        self._check_acyclic()

    def _check_acyclic(self) -> None:
        visiting, visited = set(), set()

        def visit(step_id: str, path: tuple[str, ...]) -> None:
            if step_id in visited:
                return
            if step_id in visiting:
                raise WorkflowError(f"cycle in workflow steps: {' -> '.join(path + (step_id,))}")
            visiting.add(step_id)
            for dep in self._by_id[step_id].inputs:
                visit(dep, path + (step_id,))
            visiting.discard(step_id)
            visited.add(step_id)

        for step_id in self._by_id:
            visit(step_id, ())

    @classmethod
    def load(cls, path: str | Path = "config/workflow.yaml") -> "WorkflowRegistry":
        raw = yaml.safe_load(Path(path).read_text()) or {}
        steps = [
            Step(
                id=s["id"],
                owner=s["owner"],
                inputs=tuple(s.get("inputs") or []),
                artifact=s["artifact"],
                automation=s.get("automation"),
                optional=bool(s.get("optional", False)),
            )
            for s in raw.get("steps", [])
        ]
        return cls(steps)

    def __getitem__(self, step_id: str) -> Step:
        return self._by_id[step_id]

    def __contains__(self, step_id: str) -> bool:
        return step_id in self._by_id

    def all_steps(self) -> list[Step]:
        return list(self._by_id.values())

    def _transitive_inputs(self, step_id: str) -> set[str]:
        """All ancestors of step_id (steps it depends on, directly or indirectly)."""
        seen: set[str] = set()
        stack = list(self._by_id[step_id].inputs)
        while stack:
            dep = stack.pop()
            if dep in seen:
                continue
            seen.add(dep)
            stack.extend(self._by_id[dep].inputs)
        return seen

    def _topological_order(self) -> list[Step]:
        ordered, seen = [], set()

        def visit(step_id: str) -> None:
            if step_id in seen:
                return
            seen.add(step_id)
            for dep in self._by_id[step_id].inputs:
                visit(dep)
            ordered.append(self._by_id[step_id])

        for step_id in self._by_id:
            visit(step_id)
        return ordered

    def resolve_selection(self, *, from_step: str | None = None,
                           steps: list[str] | None = None) -> "Selection":
        """Compute (selected steps, artifact-only input steps) for `workflow start`.

        --from X treats every ancestor of X as already done (artifact-only), then
        grows the selection to every step whose inputs are all satisfied — including
        steps parallel to X that share those same satisfied ancestors (e.g. starting
        from solution_requirements also pulls in test_case_specification/test_automation,
        since both only need data_design_solution/technical_design_solution).
        """
        explicit = list(steps or [])
        for step_id in [from_step, *explicit]:
            if step_id and step_id not in self:
                raise WorkflowError(f"unknown step: {step_id}")

        if not from_step and not explicit:
            selected = {s.id for s in self.all_steps() if not s.optional}
        else:
            satisfied = self._transitive_inputs(from_step) if from_step else set()
            avail: set[str] = set(satisfied)
            selected: set[str] = set()
            if from_step:
                selected.add(from_step)
                avail.add(from_step)
            for step_id in explicit:
                selected.add(step_id)
                avail.add(step_id)

            changed = True
            while changed:
                changed = False
                for s in self._by_id.values():
                    if s.id in selected or s.id in satisfied:
                        continue
                    if s.optional and s.id not in explicit:
                        continue
                    if set(s.inputs) <= avail:
                        selected.add(s.id)
                        avail.add(s.id)
                        changed = True

        artifact_only = {
            dep
            for step_id in selected
            for dep in self._by_id[step_id].inputs
            if dep not in selected
        }
        ordered = [s for s in self._topological_order() if s.id in selected | artifact_only]
        return Selection(
            selected=[s for s in ordered if s.id in selected],
            artifact_only=[s for s in ordered if s.id in artifact_only],
        )


@dataclass
class Selection:
    selected: list[Step]
    artifact_only: list[Step]
