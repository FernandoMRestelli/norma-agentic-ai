from langgraph.checkpoint.memory import InMemorySaver

from internal.agent.norma_agent import NormaAgent


def test_agent_loads_system_prompt():
    agent = NormaAgent(model=None, checkpointer=InMemorySaver())
    assert "Nunca inventes un SKU" in agent.system_prompt
