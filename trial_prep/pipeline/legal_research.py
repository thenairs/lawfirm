"""Stage 12: legal research (RAG), two calls per design-spec section 4.6.

IMPORTANT: legal_corpus/ ships with a small set of real, well-known IPC /
CrPC / Evidence Act provisions for demo purposes, and an EMPTY precedents
file. This stage never invents a citation -- if retrieval returns nothing,
the report says so explicitly. Wire legal_index.retrieve() to a real
vector store over a licensed corpus (Indian Kanoon, SCC Online, Manupatra)
before relying on this for actual case law research.
"""
import json

from .audit import log_event
from .config import StageError, call_structured
from .legal_index import retrieve

QUERY_SYSTEM_PROMPT = """Given case facts and charged sections, generate 3-6
focused search queries to retrieve relevant statutes and precedent
judgments from an indexed legal corpus. Prioritize the actual charged
sections and the most legally significant disputed facts."""

QUERY_SCHEMA = {
    "type": "object",
    "properties": {"queries": {"type": "array", "items": {"type": "string"}}},
    "required": ["queries"],
}

SYNTHESIS_SYSTEM_PROMPT = """You are synthesizing legal research for a trial
brief. You are given retrieved passages, each with a citation. Use ONLY
these passages -- never cite a provision or case that is not present in the
retrieved set. If a query's retrieval returned nothing relevant, say so
explicitly in gaps instead of filling the space with outside knowledge."""

SYNTHESIS_SCHEMA = {
    "type": "object",
    "properties": {
        "applicable_provisions": {
            "type": "array",
            "items": {"type": "object", "properties": {
                "citation": {"type": "string"}, "relevance": {"type": "string"}}},
        },
        "precedents": {
            "type": "array",
            "items": {"type": "object", "properties": {
                "citation": {"type": "string"}, "holding": {"type": "string"},
                "relevance_to_case": {"type": "string"}}},
        },
        "gaps": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["applicable_provisions", "precedents", "gaps"],
}


def run_legal_research(case_file: dict) -> dict:
    case_id = case_file["case_id"]
    docs = [d for d in case_file["documents"] if d.get("extraction")]
    key_facts = [f for d in docs for f in d["extraction"]["key_facts"]]
    charges = [s for d in docs if d.get("category") == "Charge Sheet" for s in d["extraction"]["legal_sections"]]

    if not key_facts and not charges:
        log_event(case_id, "legal_research", "skipped", reason="no facts or charges extracted")
        return {"applicable_provisions": [], "precedents": [], "gaps": ["No case facts available to research."]}

    try:
        query_result = call_structured(
            system=QUERY_SYSTEM_PROMPT,
            user_content=f"case_facts: {json.dumps(key_facts)}\ncharges: {json.dumps(charges)}",
            tool_name="generate_queries",
            tool_description="Record the search queries.",
            input_schema=QUERY_SCHEMA,
            max_tokens=1024,
        )
    except StageError as exc:
        log_event(case_id, "legal_research", "error", stage="query_generation", error=str(exc))
        return {"applicable_provisions": [], "precedents": [], "gaps": [f"Query generation failed: {exc}"]}

    retrieved = []
    seen_citations = set()
    for query in query_result["queries"]:
        for hit in retrieve(query):
            if hit["citation"] not in seen_citations:
                seen_citations.add(hit["citation"])
                retrieved.append({"query": query, **hit})

    log_event(case_id, "legal_research", "retrieved", queries=len(query_result["queries"]), hits=len(retrieved))

    if not retrieved:
        return {
            "applicable_provisions": [],
            "precedents": [],
            "gaps": ["No matching provisions or precedents found in the indexed corpus for this case's facts."],
        }

    try:
        result = call_structured(
            system=SYNTHESIS_SYSTEM_PROMPT,
            user_content=f"retrieved_passages:\n{json.dumps(retrieved, indent=2)}",
            tool_name="synthesize_research",
            tool_description="Record the synthesized legal research.",
            input_schema=SYNTHESIS_SCHEMA,
            max_tokens=3072,
        )
        log_event(case_id, "legal_research", "ok", provisions=len(result["applicable_provisions"]))
        return result
    except StageError as exc:
        log_event(case_id, "legal_research", "error", stage="synthesis", error=str(exc))
        return {"applicable_provisions": [], "precedents": [], "gaps": [f"Synthesis failed: {exc}"]}
