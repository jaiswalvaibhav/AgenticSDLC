"""Local AgentRuntime (profile=local): dispatches to the analyst task registry
(analyst/tasks.py). The aws profile's AgentRuntime (AgentCore) is built in Phase 7.
"""
from sdlc.agents.analyst.tasks import TASKS
from sdlc.ports import DocumentSource, KnowledgeIndex, ObjectStore, TicketSystem


class LocalAgentRuntime:
    def __init__(self, *, doc_source: DocumentSource, knowledge_index: KnowledgeIndex,
                 tickets: TicketSystem, store: ObjectStore, cfg: dict):
        self.doc_source = doc_source
        self.knowledge_index = knowledge_index
        self.tickets = tickets
        self.store = store
        self.cfg = cfg

    def run(self, task_id: str, context: dict) -> dict:
        task = TASKS.get(task_id)
        if not task or not task.run:
            raise ValueError(f"no runnable analyst task registered for {task_id!r}")
        return task.run(context, doc_source=self.doc_source, knowledge_index=self.knowledge_index,
                         tickets=self.tickets, store=self.store, cfg=self.cfg,
                         dry_run=context.get("dry_run", True))
