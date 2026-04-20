"""Minimal KG builder template for Assignment 4.

Keep this contract unchanged:
- Graph: (Regulation)-[:HAS_ARTICLE]->(Article)-[:CONTAINS_RULE]->(Rule)
- Article: number, content, reg_name, category
- Rule: rule_id, type, action, result, art_ref, reg_name
- Fulltext indexes: article_content_idx, rule_idx
- SQLite file: ncu_regulations.db
"""

import os
import re
import sqlite3
from hashlib import md5
from typing import Any

from dotenv import load_dotenv
from neo4j import GraphDatabase

# Keep these imports in case your project expects them.
# We will not force model loading during KG build, so the script can run first.
from llm_loader import load_local_llm, get_tokenizer, get_raw_pipeline


# ========== 0) Initialization ==========
load_dotenv()

URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
AUTH = (
    os.getenv("NEO4J_USER", "neo4j"),
    os.getenv("NEO4J_PASSWORD", "password"),
)


def extract_entities(article_number: str, reg_name: str, content: str) -> dict[str, Any]:
    """
    Minimal working version.

    For now, return deterministic fallback rules so build_kg.py can reliably
    create Rule nodes. You can later replace this with real LLM extraction.
    """
    rules = build_fallback_rules(article_number, content)
    return {"rules": rules}


def build_fallback_rules(article_number: str, content: str) -> list[dict[str, str]]:
    """
    Deterministic fallback rule extraction.

    Strategy:
    - normalize article text
    - split into sentence-like segments
    - infer a coarse rule type from keywords
    - create at least one Rule object per meaningful segment
    """
    text = " ".join((content or "").split()).strip()
    if not text:
        return []

    # Split article into clauses/sentences.
    segments = re.split(r"(?<=[\.;:])\s+|\n+", text)
    segments = [seg.strip(" \t\r\n.;:") for seg in segments if seg.strip(" \t\r\n.;:")]

    # If splitting fails badly, keep whole article as one segment.
    if not segments:
        segments = [text]

    rules: list[dict[str, str]] = []

    for seg in segments:
        lower = seg.lower()

        # Order matters: prohibition before permission/obligation.
        if any(k in lower for k in ["must not", "shall not", "may not", "prohibited", "forbidden", "no person shall"]):
            rule_type = "prohibition"
        elif any(k in lower for k in ["must", "shall", "required", "require"]):
            rule_type = "obligation"
        elif any(k in lower for k in ["may", "can", "permitted", "allowed"]):
            rule_type = "permission"
        elif any(k in lower for k in ["penalty", "fine", "punish", "violate", "violation", "liable"]):
            rule_type = "penalty"
        else:
            rule_type = "general"

        action = seg
        result = seg

        # Try a rough action/result split.
        m = re.search(
            r"(.+?)\b(shall be|must be|will be|is subject to|subject to|liable to|punishable by|may be)\b(.+)",
            seg,
            flags=re.I,
        )
        if m:
            left = m.group(1).strip(" ,.;")
            right = (m.group(2) + " " + m.group(3)).strip(" ,.;")
            if left:
                action = left
            if right:
                result = right

        # Keep both required fields non-empty.
        action = action.strip()
        result = result.strip()
        if not action or not result:
            continue

        rules.append(
            {
                "type": rule_type,
                "action": action[:1000],
                "result": result[:1000],
                "art_ref": str(article_number),
            }
        )

    # Deduplicate within one article.
    seen = set()
    deduped = []
    for rule in rules:
        key = (
            rule.get("type", "").strip().lower(),
            rule.get("action", "").strip().lower(),
            rule.get("result", "").strip().lower(),
            rule.get("art_ref", "").strip().lower(),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(rule)

    return deduped


# SQLite tables used:
# - regulations(reg_id, name, category)
# - articles(reg_id, article_number, content)


def build_graph() -> None:
    """Build KG from SQLite into Neo4j using the fixed assignment schema."""
    sql_conn = sqlite3.connect("ncu_regulations.db")
    cursor = sql_conn.cursor()
    driver = GraphDatabase.driver(URI, auth=AUTH)

    # Optional: If later you want to use actual local LLM extraction,
    # uncomment the next line. For now, keep it disabled so build is stable.
    # load_local_llm()

    with driver.session() as session:
        # Fixed strategy: clear existing graph data before rebuilding.
        session.run("MATCH (n) DETACH DELETE n")

        # 1) Read regulations and create Regulation nodes.
        cursor.execute("SELECT reg_id, name, category FROM regulations")
        regulations = cursor.fetchall()
        reg_map: dict[int, tuple[str, str]] = {}

        for reg_id, name, category in regulations:
            reg_map[reg_id] = (name, category)
            session.run(
                "MERGE (r:Regulation {id:$rid}) SET r.name=$name, r.category=$cat",
                rid=reg_id,
                name=name,
                cat=category,
            )

        # 2) Read articles and create Article + HAS_ARTICLE.
        cursor.execute("SELECT reg_id, article_number, content FROM articles")
        articles = cursor.fetchall()

        for reg_id, article_number, content in articles:
            reg_name, reg_category = reg_map.get(reg_id, ("Unknown", "Unknown"))
            session.run(
                """
                MATCH (r:Regulation {id: $rid})
                CREATE (a:Article {
                    number:   $num,
                    content:  $content,
                    reg_name: $reg_name,
                    category: $reg_category
                })
                MERGE (r)-[:HAS_ARTICLE]->(a)
                """,
                rid=reg_id,
                num=article_number,
                content=content,
                reg_name=reg_name,
                reg_category=reg_category,
            )

        # 3) Create full-text index on Article content.
        session.run(
            """
            CREATE FULLTEXT INDEX article_content_idx IF NOT EXISTS
            FOR (a:Article) ON EACH [a.content]
            """
        )

        rule_counter = 0
        seen_rule_keys = set()

        # 4) Iterate through all articles, extract rules, and create Rule nodes.
        for reg_id, article_number, content in articles:
            reg_name, _ = reg_map.get(reg_id, ("Unknown", "Unknown"))

            extracted = extract_entities(str(article_number), reg_name, content)
            rules = extracted.get("rules", []) if isinstance(extracted, dict) else []

            # Double fallback, just in case.
            if not rules:
                rules = build_fallback_rules(str(article_number), content)

            for rule in rules:
                action = str(rule.get("action", "")).strip()
                result = str(rule.get("result", "")).strip()

                # Skip invalid rules with empty action/result.
                if not action or not result:
                    continue

                rule_type = str(rule.get("type", "general")).strip() or "general"
                art_ref = str(rule.get("art_ref", article_number)).strip() or str(article_number)

                # Logical deduplication key.
                dedup_key = (
                    reg_name.lower(),
                    art_ref.lower(),
                    rule_type.lower(),
                    action.lower(),
                    result.lower(),
                )
                if dedup_key in seen_rule_keys:
                    continue
                seen_rule_keys.add(dedup_key)

                # Stable unique rule_id.
                digest = md5("||".join(dedup_key).encode("utf-8")).hexdigest()[:12]
                rule_id = f"R-{art_ref}-{digest}"

                session.run(
                    """
                    MATCH (a:Article {
                        number: $article_number,
                        reg_name: $reg_name
                    })
                    MERGE (r:Rule {rule_id: $rule_id})
                    SET r.type = $rule_type,
                        r.action = $action,
                        r.result = $result,
                        r.art_ref = $art_ref,
                        r.reg_name = $reg_name
                    MERGE (a)-[:CONTAINS_RULE]->(r)
                    """,
                    article_number=article_number,
                    reg_name=reg_name,
                    rule_id=rule_id,
                    rule_type=rule_type,
                    action=action,
                    result=result,
                    art_ref=art_ref,
                )
                rule_counter += 1

        print(f"[Rules] created={rule_counter}")

        # 5) Create full-text index on Rule fields.
        session.run(
            """
            CREATE FULLTEXT INDEX rule_idx IF NOT EXISTS
            FOR (r:Rule) ON EACH [r.action, r.result]
            """
        )

        # 6) Coverage audit.
        coverage = session.run(
            """
            MATCH (a:Article)
            OPTIONAL MATCH (a)-[:CONTAINS_RULE]->(r:Rule)
            WITH a, count(r) AS rule_count
            RETURN count(a) AS total_articles,
                   sum(CASE WHEN rule_count > 0 THEN 1 ELSE 0 END) AS covered_articles,
                   sum(CASE WHEN rule_count = 0 THEN 1 ELSE 0 END) AS uncovered_articles
            """
        ).single()

        total_articles = int((coverage or {}).get("total_articles", 0) or 0)
        covered_articles = int((coverage or {}).get("covered_articles", 0) or 0)
        uncovered_articles = int((coverage or {}).get("uncovered_articles", 0) or 0)

        print(
            f"[Coverage] covered={covered_articles}/{total_articles}, "
            f"uncovered={uncovered_articles}"
        )

    driver.close()
    sql_conn.close()


if __name__ == "__main__":
    build_graph()