##############################################################################
# trialtrace/evidence_expansion/agent/budget.py
#
# PURPOSE: Hard limits for one outcome, enforced in code (not in the prompt).
#          Every tool call and every model call is counted. When the limit is
#          reached the agent must stop and keep the original status.
#
# USE:     budget = Budget(max_calls=15)
#          if budget.is_used_up(): stop
#          budget.add_call("fetch")
##############################################################################


class Budget:
    def __init__(self, max_calls: int = 15):
        self.max_calls = max_calls
        self.calls = 0
        self.fetches = 0
        self.model_calls = 0
        self.tried_queries = set()          # a search is never repeated

    def is_used_up(self) -> bool:
        return self.calls >= self.max_calls

    def add_call(self, kind: str) -> None:
        """kind is 'fetch' (tool call to a source) or 'llm' (a model call)."""
        self.calls += 1
        if kind == "llm":
            self.model_calls += 1
        else:
            self.fetches += 1

    def already_tried(self, query: str) -> bool:
        """True if this exact query was used before. Otherwise remember it and return False."""
        key = query.strip().lower()
        if key in self.tried_queries:
            return True
        self.tried_queries.add(key)
        return False
