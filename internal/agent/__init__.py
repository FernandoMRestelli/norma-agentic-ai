"""Agente Norma. Este archivo corre antes de importar LangGraph, que lee la variable al importarse."""

import os

# Al leer el estado guardado, LangGraph solo reconstruye tipos conocidos (spec del agente §3.2)
os.environ.setdefault("LANGGRAPH_STRICT_MSGPACK", "true")
