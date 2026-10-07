"""Seeds the demo Confluence page tree for a use case: dummy content (Jinja templates)
plus programmatic diagrams (Graphviz), created under the configured Confluence space.
Dry-run by default; idempotent on re-run (an existing page with the same title is left
alone, found via DocumentSource.find_page_by_title). See docs/BRIEF.md "Demo use case".
"""
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader

from sdlc import diagrams
from sdlc.ports import DocumentSource


@dataclass(frozen=True)
class SeedPage:
    role: str                      # key in page_roles.yaml
    title: str
    template: str                  # filename under templates/pages/
    parent_role: str | None = None  # None = top-level page in the space
    diagram: str | None = None      # None, or a key in diagrams.RENDERERS
    extra_context: dict = field(default_factory=dict)


@dataclass
class SeedResult:
    created: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)  # already existed, by title


def _pages(terms: dict, usecase: dict) -> list[SeedPage]:
    """The seed page tree, in creation order. Built from `terms` so page titles follow
    the use case's terminology (identity-mapped for the demo use case)."""
    return [
        SeedPage("usecase_root", usecase["display_name"], "usecase_root.html.j2"),
        SeedPage("frontdoor_request", terms["frontdoor_request"], "frontdoor_request.html.j2",
                  "usecase_root"),
        SeedPage("stakeholder_requirements", terms["stakeholder_requirements"],
                  "stakeholder_requirements.html.j2", "frontdoor_request"),
        SeedPage("scope", terms["scope"], "scope.html.j2", "stakeholder_requirements"),
        SeedPage("feasibility_analysis", terms["feasibility_analysis"], "feasibility_analysis.html.j2",
                  "scope"),
        SeedPage("source_data_analysis", terms["source_data_analysis"], "source_data_analysis.html.j2",
                  "scope"),
        SeedPage("conceptual_data_model", terms["conceptual_data_model"], "conceptual_data_model.html.j2",
                  "source_data_analysis", diagram="conceptual_data_model"),
        SeedPage("solution_architecture", terms["solution_architecture"], "solution_architecture.html.j2",
                  "conceptual_data_model", diagram="solution_architecture"),
        SeedPage("data_design_solution", terms["data_design_solution"], "data_design_solution.html.j2",
                  "solution_architecture"),
        SeedPage("technical_design_solution", terms["technical_design_solution"],
                  "technical_design_solution.html.j2", "solution_architecture",
                  diagram="technical_design_solution"),
        SeedPage("data_contract", terms["data_contract"], "placeholder.html.j2", "data_design_solution",
                  extra_context={"title": terms["data_contract"],
                                  "note": "Produced from the Data Design Solution by a future task."}),
        SeedPage("test_strategy", terms["test_strategy"], "placeholder.html.j2",
                  "technical_design_solution",
                  extra_context={"title": terms["test_strategy"],
                                  "note": "Produced by the (out-of-scope) tester agent."}),
    ]


def seed_usecase(doc_source: DocumentSource, usecase_dir: str | Path, *, dry_run: bool = True) -> SeedResult:
    usecase_dir = Path(usecase_dir)
    usecase = yaml.safe_load((usecase_dir / "usecase.yaml").read_text())
    terminology = yaml.safe_load((usecase_dir / "terminology.yaml").read_text())
    roles_path = usecase_dir / "page_roles.yaml"
    page_roles = yaml.safe_load(roles_path.read_text()) if roles_path.exists() else {"roles": {}}

    env = Environment(loader=FileSystemLoader(str(usecase_dir / "templates" / "pages")),
                       autoescape=False)
    terms, layers = terminology["terms"], terminology["layers"]
    base_context = {"usecase": usecase, "terms": terms, "layers": layers}

    specs = _pages(terms, usecase)
    result = SeedResult()
    page_id_by_role: dict[str, str] = {}

    for spec in specs:
        parent_id = page_id_by_role.get(spec.parent_role) if spec.parent_role else None
        existing = doc_source.find_page_by_title(spec.title)
        if existing:
            page_id_by_role[spec.role] = existing.page_id
            result.skipped.append(spec.title)
            continue

        body = env.get_template(spec.template).render(**base_context, **spec.extra_context)
        page = doc_source.create_page(parent_id, spec.title, body, dry_run=dry_run)
        page_id_by_role[spec.role] = page.page_id
        result.created.append(spec.title)

        if spec.diagram and not dry_run:
            png = diagrams.RENDERERS[spec.diagram]()
            doc_source.add_attachment(page.page_id, f"{spec.diagram}.png", png,
                                       media_type="image/png", dry_run=dry_run)

    if not dry_run:
        for role, page_id in page_id_by_role.items():
            page_roles.setdefault("roles", {}).setdefault(role, {})["page_id"] = page_id
        roles_path.write_text(yaml.safe_dump(page_roles, sort_keys=False))

    return result
