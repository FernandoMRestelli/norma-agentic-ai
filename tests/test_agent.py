from backend.agent.llm_client import LLMResponse
from backend.agent.norma_agent import NormaAgent


class FakeLLM:
    """LLM falso para testear el agente sin llamar a ningún proveedor."""

    def __init__(self, response: LLMResponse):
        self.response = response

    def chat(self, system, messages, tools=None):
        return self.response


def test_agent_loads_instructions():
    agent = NormaAgent(FakeLLM(LLMResponse(text="hola")))
    assert "Nunca inventes un SKU" in agent.instructions


# TODO: con FakeLLM, verificar que el agente pide aclaración ante productos ambiguos
