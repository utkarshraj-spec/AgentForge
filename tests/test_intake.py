from agentforge.intake import parse_prompt


def test_parse_prompt_extracts_project_name():
    spec = parse_prompt("Build a todo app with user authentication and a REST API")
    assert spec.project_name
    assert spec.functional_requirements
    assert any("secur" in c.kind or "secur" in c.description.lower() for c in spec.constraints)


def test_parse_prompt_sets_raw_prompt():
    prompt = "Build a chat service with websocket support"
    spec = parse_prompt(prompt)
    assert spec.raw_prompt == prompt
    assert spec.target_stack  # non-empty default
