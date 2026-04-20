"""Minimal KG query template for Assignment 4.

Keep these APIs unchanged for auto-test:
- generate_text(messages, max_new_tokens=220)
- get_relevant_articles(question)
- generate_answer(question, rule_results)

Keep Rule fields aligned with build_kg output:
rule_id, type, action, result, art_ref, reg_name
"""

import os
import re
from typing import Any

from neo4j import GraphDatabase
from dotenv import load_dotenv

from llm_loader import load_local_llm, get_tokenizer, get_raw_pipeline


# ========== 0) Initialization ==========
load_dotenv()

URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
AUTH = (
    os.getenv("NEO4J_USER", "neo4j"),
    os.getenv("NEO4J_PASSWORD", "password"),
)

for key in ["http_proxy", "https_proxy", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY"]:
    if key in os.environ:
        del os.environ[key]

try:
    driver = GraphDatabase.driver(URI, auth=AUTH)
    driver.verify_connectivity()
except Exception as e:
    print(f"⚠️ Neo4j connection warning: {e}")
    driver = None


# ========== helpers ==========
STOPWORDS = {
    "what", "is", "the", "for", "a", "an", "of", "to", "in", "on", "at", "by",
    "can", "i", "be", "before", "after", "during", "with", "and", "or", "are",
    "am", "do", "does", "did", "if", "my", "me", "it", "they", "their", "we",
    "our", "you", "your", "from", "this", "that", "these", "those", "how",
    "many", "much", "long", "minutes", "minute"
}
def rerank_results(question: str, results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    q = normalize_question(question)

    for item in results:
        text = " ".join([
            str(item.get("action", "")),
            str(item.get("result", "")),
            str(item.get("article_content", "")),
            str(item.get("reg_name", "")),
        ]).lower()

        score = int(item.get("score", 0) or 0)

        # education level control
        if "undergraduate" in q:
            if "undergraduate" in text or "bachelor" in text:
                score += 8
            if "postgraduate" in text or "graduate student" in text:
                score -= 10

        if "postgraduate" in q or "graduate" in q:
            if "postgraduate" in text or "graduate student" in text:
                score += 8
            if "undergraduate" in text or "bachelor" in text:
                score -= 10

        # Q15-like: extension / study duration
        if "extension" in q or "study duration" in q:
            for kw in ["extension", "extend", "extended", "study duration", "years", "semester"]:
                if kw in text:
                    score += 3

        # Q16-like: passing score
        if "passing score" in q or "pass mark" in q:
            for kw in ["passing score", "pass", "score", "grade", "seventy", "60", "70"]:
                if kw in text:
                    score += 3

        item["_rerank_score"] = score

    return sorted(results, key=lambda x: x.get("_rerank_score", 0), reverse=True)


def normalize_question(question: str) -> str:
    q = question.lower().strip()

    replacements = {
        "barred from the exam": "not allowed to take the exam",
        "barred from exam": "not allowed to take the exam",
        "leave the exam room": "leave exam room",
        "student id": "id card",
        "electronic devices with communication capabilities": "electronic devices communication",
        "cheating": "copying passing notes misconduct",
        "late": "late tardy",
        "penalty": "penalty punishment sanction",
        "exam": "exam examination test",
        "undergraduate": "undergraduate bachelor",
    	"postgraduate": "postgraduate graduate master doctoral",
    	"study duration": "study duration period of study enrollment duration",
   	 	"extension period": "extension extend extended additional years semester",
    	"maximum extension period": "maximum extension extend maximum additional years",
    	"passing score": "passing score pass mark passing grade minimum score",
    	"barred from the exam": "not allowed to take the exam",
    	"leave the exam room": "leave exam room",
    	"student id": "id card",
    	"electronic devices with communication capabilities": "electronic devices communication",
	}
	

    for src, dst in replacements.items():
        q = q.replace(src, dst)

    return q


def tokenize_keywords(question: str) -> list[str]:
    q = normalize_question(question)
    words = re.findall(r"[a-zA-Z0-9]+", q)
    words = [w for w in words if w not in STOPWORDS and len(w) >= 2]
    return words


# ========== 1) Public API ==========
def generate_answer(question: str, rule_results: list[dict[str, Any]]) -> str:
    """
    Generate a grounded answer from retrieved rules.
    Strategy:
    1. If no evidence, return fixed fallback immediately.
    2. Rerank results using question-aware heuristics.
    3. For numeric / duration / score questions, try to extract the most relevant
       number-bearing phrase instead of blindly returning the whole result.
    4. Otherwise prefer direct rule result, then action.
    5. Only call LLM as a last resort.
    """
    if not rule_results:
        return "Insufficient rule evidence to answer this question."

    # Re-rank once more before answering
    try:
        ranked = rerank_results(question, rule_results)
    except Exception:
        ranked = rule_results

    top = ranked[0]

    action = str(top.get("action") or "").strip()
    result = str(top.get("result") or "").strip()
    article_no = str(top.get("art_ref") or top.get("article_number") or "").strip()
    reg_name = str(top.get("reg_name") or "").strip()
    article_content = str(top.get("article_content") or "").strip()

    q = normalize_question(question)
    combined = " ".join([action, result, article_content]).strip()

    def wrap_answer(text: str) -> str:
        text = text.strip().rstrip(".")
        if article_no and reg_name:
            return f"{text}. (Article {article_no}, {reg_name})"
        if article_no:
            return f"{text}. (Article {article_no})"
        return f"{text}."

    # ---------- 1) Passing score questions ----------
    if "passing score" in q or "pass mark" in q or "minimum score" in q:
        # Try to prefer evidence matching the education level
        for item in ranked:
            txt = " ".join([
                str(item.get("action", "")),
                str(item.get("result", "")),
                str(item.get("article_content", "")),
            ]).lower()

            if "undergraduate" in q and ("postgraduate" in txt or "graduate student" in txt):
                continue
            if ("postgraduate" in q or "graduate" in q) and ("undergraduate" in txt or "bachelor" in txt):
                continue

            m = re.search(
                r'((?:sixty|seventy|eighty|ninety|one hundred|\d+)\s*(?:points?|score)?[^.]{0,60}?(?:passing score|pass(?:ing)? grade|minimum passing score|minimum score))',
                txt,
                flags=re.I,
            )
            if m:
                return wrap_answer(m.group(1))

            m2 = re.search(
                r'((?:passing score|pass(?:ing)? grade|minimum passing score|minimum score)[^.]{0,60}?(?:sixty|seventy|eighty|ninety|one hundred|\d+))',
                txt,
                flags=re.I,
            )
            if m2:
                return wrap_answer(m2.group(1))

        # fallback to top result if it mentions score/pass
        if any(k in combined.lower() for k in ["passing score", "pass", "score", "grade"]):
            return wrap_answer(result or action)

    # ---------- 2) Study duration / extension questions ----------
    if "extension" in q or "study duration" in q or "period of study" in q:
        for item in ranked:
            txt = " ".join([
                str(item.get("action", "")),
                str(item.get("result", "")),
                str(item.get("article_content", "")),
            ])

            low = txt.lower()

            if "undergraduate" in q and ("postgraduate" in low or "graduate student" in low):
                continue
            if ("postgraduate" in q or "graduate" in q) and ("undergraduate" in low or "bachelor" in low):
                continue

            m = re.search(
                r'((?:\d+|one|two|three|four|five|six|seven|eight|nine|ten)\s+(?:academic\s+)?(?:year|years|semester|semesters))',
                txt,
                flags=re.I,
            )
            if m:
                # Prefer phrases near extension-related keywords
                snippet = m.group(1)
                nearby = re.search(
                    r'((?:maximum\s+)?(?:extension|extended|extend|period of study|study duration)[^.]{0,80}?(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten)\s+(?:academic\s+)?(?:year|years|semester|semesters))',
                    txt,
                    flags=re.I,
                )
                if nearby:
                    return wrap_answer(nearby.group(1))
                return wrap_answer(snippet)

        if result:
            return wrap_answer(result)

    # ---------- 3) Late / minutes / exam-room style numeric questions ----------
    if any(k in q for k in ["late", "minutes", "minute", "exam room", "leave exam room", "barred from the exam"]):
        for item in ranked:
            txt = " ".join([
                str(item.get("action", "")),
                str(item.get("result", "")),
                str(item.get("article_content", "")),
            ])

            m = re.search(
                r'((?:\d+|one|two|three|four|five|ten|fifteen|twenty|thirty|forty|fifty|sixty)\s+minutes?[^.]{0,80})',
                txt,
                flags=re.I,
            )
            if m:
                return wrap_answer(m.group(1))

        if result:
            return wrap_answer(result)

    # ---------- 4) Penalty questions ----------
    if any(k in q for k in ["penalty", "punishment", "sanction", "fine"]):
        for item in ranked:
            txt = " ".join([
                str(item.get("action", "")),
                str(item.get("result", "")),
                str(item.get("article_content", "")),
            ])

            m = re.search(
                r'((?:shall be|will be|is subject to|subject to|liable to|punishable by)[^.]{0,120})',
                txt,
                flags=re.I,
            )
            if m:
                return wrap_answer(m.group(1))

        if result:
            return wrap_answer(result)

    # ---------- 5) Permission / yes-no questions ----------
    if any(k in q for k in ["can ", "may ", "allowed", "permit", "permitted"]):
        top_text = " ".join([action, result]).lower()

        if any(k in top_text for k in ["shall not", "must not", "may not", "not allowed", "prohibited", "forbidden"]):
            return wrap_answer("No")
        if any(k in top_text for k in ["may", "allowed", "permitted", "can"]):
            return wrap_answer("Yes")

        if result:
            return wrap_answer(result)

    # ---------- 6) Best-effort direct answer without LLM ----------
    if result:
        return wrap_answer(result)
    if action:
        return wrap_answer(action)

    # ---------- 7) LLM fallback only if needed ----------
    evidence_lines = []
    for i, item in enumerate(ranked[:4], start=1):
        evidence_lines.append(
            f"{i}. Article {item.get('art_ref') or item.get('article_number')}, "
            f"{item.get('reg_name')}: "
            f"action={item.get('action', '')}; result={item.get('result', '')}"
        )
    evidence_text = "\n".join(evidence_lines)

    messages = [
        {
            "role": "system",
            "content": (
                "You are a careful university regulation assistant. "
                "Answer using ONLY the provided rule evidence. "
                "Be brief and factual. "
                "If the evidence is insufficient, say exactly: "
                "'Insufficient rule evidence to answer this question.'"
            ),
        },
        {
            "role": "user",
            "content": (
                f"Question: {question}\n\n"
                f"Rule Evidence:\n{evidence_text}\n\n"
                "Instructions:\n"
                "1. Give a direct answer in 1-2 sentences.\n"
                "2. Prefer exact numbers, time limits, penalties, or permissions when stated.\n"
                "3. Do not invent facts.\n"
            ),
        },
    ]

    try:
        return generate_text(messages, max_new_tokens=48)
    except Exception:
        return "Insufficient rule evidence to answer this question."
def extract_entities(question: str) -> dict[str, Any]:
    q = normalize_question(question)
    terms = tokenize_keywords(q)

    question_type = "general"
    aspect = "general"

    if any(x in q for x in ["penalty", "punishment", "sanction", "fine"]):
        question_type = "penalty"
        aspect = "penalty"
    elif any(x in q for x in ["how many", "how long", "minutes", "days", "hours"]):
        question_type = "numeric"
        aspect = "time_or_number"
    elif any(x in q for x in ["can", "may", "allowed", "permit"]):
        question_type = "permission"
        aspect = "permission"

    return {
        "question_type": question_type,
        "subject_terms": terms[:8],
        "aspect": aspect,
        "normalized_question": q,
    }
def generate_text(messages: list[dict[str, str]], max_new_tokens: int = 80) -> str:
    """
    Call local HF model via chat template + raw pipeline.
    """
    tok = get_tokenizer()
    pipe = get_raw_pipeline()

    if tok is None or pipe is None:
        load_local_llm()
        tok = get_tokenizer()
        pipe = get_raw_pipeline()

    prompt = tok.apply_chat_template(
        messages,
        tokenize=False,
        add_generation_prompt=True
    )

    return pipe(
        prompt,
        max_new_tokens=max_new_tokens
    )[0]["generated_text"].strip()

def build_typed_cypher(entities: dict[str, Any]) -> tuple[str, str]:
    cypher_typed = """
    MATCH (a:Article)-[:CONTAINS_RULE]->(r:Rule)
    WHERE
        ANY(term IN $terms WHERE
            toLower(r.action) CONTAINS term OR
            toLower(r.result) CONTAINS term OR
            toLower(a.content) CONTAINS term OR
            toLower(r.reg_name) CONTAINS term
        )
    RETURN
        r.rule_id AS rule_id,
        r.type AS type,
        r.action AS action,
        r.result AS result,
        r.art_ref AS art_ref,
        r.reg_name AS reg_name,
        a.number AS article_number,
        a.content AS article_content,
        size([term IN $terms WHERE
            toLower(r.action) CONTAINS term OR
            toLower(r.result) CONTAINS term OR
            toLower(a.content) CONTAINS term OR
            toLower(r.reg_name) CONTAINS term
        ]) AS score
    ORDER BY score DESC, article_number ASC
    LIMIT 8
    """

    cypher_broad = """
    MATCH (a:Article)
    WHERE
        ANY(term IN $terms WHERE
            toLower(a.content) CONTAINS term OR
            toLower(a.reg_name) CONTAINS term
        )
    OPTIONAL MATCH (a)-[:CONTAINS_RULE]->(r:Rule)
    RETURN
        r.rule_id AS rule_id,
        r.type AS type,
        r.action AS action,
        r.result AS result,
        COALESCE(r.art_ref, a.number) AS art_ref,
        COALESCE(r.reg_name, a.reg_name) AS reg_name,
        a.number AS article_number,
        a.content AS article_content,
        size([term IN $terms WHERE
            toLower(a.content) CONTAINS term OR
            toLower(a.reg_name) CONTAINS term
        ]) AS score
    ORDER BY score DESC, article_number ASC
    LIMIT 8
    """

    return cypher_typed, cypher_broad


def get_relevant_articles(question: str) -> list[dict[str, Any]]:
    if driver is None:
        return []

    entities = extract_entities(question)
    terms = entities.get("subject_terms", [])
    if not terms:
        return []

    cypher_typed, cypher_broad = build_typed_cypher(entities)

    merged: list[dict[str, Any]] = []
    seen = set()

    with driver.session() as session:
        # 1) typed rule-first retrieval
        rows = session.run(cypher_typed, terms=terms)
        for row in rows:
            item = dict(row)
            key = (
                item.get("rule_id"),
                item.get("article_number"),
                item.get("action"),
                item.get("result"),
            )
            if key in seen:
                continue
            seen.add(key)
            merged.append(item)

        # 2) fallback broad article retrieval
        if len(merged) < 3:
            rows = session.run(cypher_broad, terms=terms)
            for row in rows:
                item = dict(row)
                key = (
                    item.get("rule_id"),
                    item.get("article_number"),
                    item.get("action"),
                    item.get("result"),
                )
                if key in seen:
                    continue
                seen.add(key)
                merged.append(item)

    merged.sort(key=lambda x: x.get("score", 0), reverse=True)
    return merged[:6]


def generate_answer(question: str, rule_results: list[dict[str, Any]]) -> str:
    # Fast exit: do not call LLM when nothing was retrieved.
    if not rule_results:
        return "Insufficient rule evidence to answer this question."

    # Fast path for numeric / penalty questions:
    top = rule_results[0]
    action = (top.get("action") or "").strip()
    result = (top.get("result") or "").strip()
    article_no = str(top.get("art_ref") or top.get("article_number") or "").strip()
    reg_name = (top.get("reg_name") or "").strip()

    # If the top hit already looks sufficient, answer directly without LLM.
    if action and result:
        direct = f"{result}. (Article {article_no}, {reg_name})"
        if len(direct) <= 220:
            return direct

    evidence_lines = []
    for i, item in enumerate(rule_results[:4], start=1):
        evidence_lines.append(
            f"{i}. Article {item.get('art_ref') or item.get('article_number')}, "
            f"{item.get('reg_name')}: "
            f"action={item.get('action', '')}; result={item.get('result', '')}"
        )

    evidence_text = "\n".join(evidence_lines)

    messages = [
        {
            "role": "system",
            "content": (
                "You are a careful university regulation assistant. "
                "Answer using ONLY the provided rule evidence. "
                "Be brief and factual. "
                "If the evidence is insufficient, say exactly: "
                "'Insufficient rule evidence to answer this question.'"
            ),
        },
        {
            "role": "user",
            "content": (
                f"Question: {question}\n\n"
                f"Rule Evidence:\n{evidence_text}\n\n"
                "Instructions:\n"
                "1. Give a direct answer in 1-2 sentences.\n"
                "2. Prefer exact numbers, time limits, penalties, or permissions when stated.\n"
                "3. Do not invent facts.\n"
            ),
        },
    ]

    return generate_text(messages, max_new_tokens=80)


def main() -> None:
    if driver is None:
        return

    load_local_llm()

    print("=" * 50)
    print("🎓 NCU Regulation Assistant")
    print("=" * 50)
    print("💡 Try: 'What is the penalty for forgetting student ID?'")
    print("👉 Type 'exit' to quit.\n")

    while True:
        try:
            user_q = input("\nUser: ").strip()
            if not user_q:
                continue
            if user_q.lower() in {"exit", "quit"}:
                print("👋 Bye!")
                break

            results = get_relevant_articles(user_q)
            answer = generate_answer(user_q, results)
            print(f"Bot: {answer}")

        except KeyboardInterrupt:
            print("\n👋 Bye!")
            break
        except Exception as e:
            print(f"❌ Error: {e}")

    driver.close()


if __name__ == "__main__":
    main()