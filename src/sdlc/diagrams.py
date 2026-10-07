"""Programmatic diagrams for the demo use case ("Order Fulfilment Performance"),
rendered with Graphviz. Dev-only: needs the `graphviz` Python package (dev dependency
group) AND the system `dot` binary (`brew install graphviz` / `apt install graphviz`).

`graphviz` is imported lazily inside each function, not at module scope, so importing
this module — or anything that imports it, like seed.py and the CLI — never fails in an
environment that only has the core dependencies (e.g. the aws profile's deploy image).
"""


def conceptual_data_model_png() -> bytes:
    import graphviz
    g = graphviz.Digraph("conceptual_data_model", format="png")
    g.attr(rankdir="LR", fontsize="10")
    for entity in ("Customer", "Order", "Order Line", "Product", "Shipment"):
        g.node(entity, shape="box", style="rounded,filled", fillcolor="#eef3fb")
    g.edge("Customer", "Order", label="places")
    g.edge("Order", "Order Line", label="contains")
    g.edge("Order Line", "Product", label="refers to")
    g.edge("Order", "Shipment", label="fulfilled by")
    return g.pipe(format="png")


def solution_architecture_png() -> bytes:
    import graphviz
    g = graphviz.Digraph("solution_architecture", format="png")
    g.attr(rankdir="LR", fontsize="10")
    with g.subgraph(name="cluster_sources") as c:
        c.attr(label="Sources", style="dashed")
        for source in ("Orders", "Shipments", "Customers", "Product Catalogue"):
            c.node(source, shape="box")
    g.node("Raw", shape="box", style="filled", fillcolor="#f6e8c3")
    g.node("Curated", shape="box", style="filled", fillcolor="#d9f0d3")
    g.node("Presentation", shape="box", style="filled", fillcolor="#d3e3f0")
    g.node("BI Dashboard", shape="box", style="rounded,filled", fillcolor="#eeeeee")
    for source in ("Orders", "Shipments", "Customers", "Product Catalogue"):
        g.edge(source, "Raw")
    g.edge("Raw", "Curated")
    g.edge("Curated", "Presentation")
    g.edge("Presentation", "BI Dashboard")
    return g.pipe(format="png")


def technical_design_solution_png() -> bytes:
    import graphviz
    g = graphviz.Digraph("technical_design_solution", format="png")
    g.attr(rankdir="LR", fontsize="10")
    g.node("Orchestrator", shape="box", style="rounded,filled", fillcolor="#eeeeee")
    g.node("Raw Zone", shape="box")
    g.node("Curated Zone", shape="box")
    g.node("Presentation Zone", shape="box")
    g.node("Data Quality Checks", shape="box", style="dashed")
    g.edge("Orchestrator", "Raw Zone", label="extract")
    g.edge("Raw Zone", "Curated Zone", label="transform")
    g.edge("Curated Zone", "Data Quality Checks")
    g.edge("Curated Zone", "Presentation Zone", label="publish")
    return g.pipe(format="png")


RENDERERS = {
    "conceptual_data_model": conceptual_data_model_png,
    "solution_architecture": solution_architecture_png,
    "technical_design_solution": technical_design_solution_png,
}
