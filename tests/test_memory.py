from agentforge.memory import SharedContext


def test_shared_context_kv_and_audit():
    ctx = SharedContext(persist_dir=None)
    ctx.put("foo", {"x": 1})
    assert ctx.get("foo") == {"x": 1}
    ctx.audit("actor", "event", {"k": "v"})
    trail = ctx.audit_trail()
    assert len(trail) == 1
    assert trail[0].actor == "actor"
    assert trail[0].event == "event"


def test_shared_context_remember_and_recall():
    ctx = SharedContext(persist_dir=None)
    ctx.remember("auth module produced auth.py", metadata={"module": "auth"})
    ctx.remember("database module produced models.py", metadata={"module": "database"})
    hits = ctx.recall("auth", top_k=1)
    assert hits
    doc, meta = hits[0]
    assert "auth" in doc
