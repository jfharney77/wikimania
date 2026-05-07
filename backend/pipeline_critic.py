import json
import re
from asyncio import Queue

import db
import llm

FIND_DUPLICATES_PROMPT = """\
You are reviewing a wiki knowledge base. Given the following list of article titles, identify groups of titles that cover the same topic and should be merged into one article.

Be liberal: merge titles that are the same concept under different names, abbreviations, acronyms, or phrasings (e.g. "Faster R-CNN" and "Faster RCNN", "ML" and "Machine Learning", "Object Detection Overview" and "Object Detection").

Article titles:
{titles}

Return ONLY a JSON array of arrays, where each inner array contains titles that should be merged.
Return [] if no duplicates are found.
Example: [["Machine Learning", "ML Overview"], ["Faster R-CNN", "Faster RCNN", "Faster R-CNN Overview"]]
No explanation, no markdown fences."""

MERGE_ARTICLES_PROMPT = """\
You are merging duplicate wiki articles about the same topic into one comprehensive article.

Keep the title: {primary_title}

Articles to merge:
{articles}

Write a single comprehensive wiki article that combines the best information from all the above articles.
Use [[Article Title]] wikilink syntax where appropriate.
Start directly with article content — no preamble."""

FIND_CONTRADICTIONS_PROMPT = """\
You are reviewing a set of wiki articles for factual contradictions.

Articles:
{articles}

Identify any direct factual contradictions BETWEEN articles (e.g., Article A says X causes Y, Article B says X does NOT cause Y).
Do NOT flag incomplete information or different levels of detail — only clear contradictions.

Return ONLY a JSON array of objects:
[{{"article": "Title of article to fix", "issue": "brief description", "fix": "corrected statement"}}]
Return [] if no contradictions found.
No explanation, no markdown fences."""

FIX_CONTRADICTION_PROMPT = """\
Update the following wiki article to fix a factual contradiction.

Article title: {title}
Current content:
{content}

Contradiction: {issue}
Correction: {fix}

Return the complete corrected article. Keep all other content intact.
Start directly with article content — no preamble."""


def _parse_json_list(raw: str) -> list:
    cleaned = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
    m = re.search(r"\[.*\]", cleaned, re.DOTALL)
    if not m:
        return []
    try:
        return json.loads(m.group())
    except json.JSONDecodeError:
        return []


async def run_critic(wiki_id: int, job_id: int, queue: Queue):
    try:
        await db.update_job_status(job_id, "running")
        articles = await db.get_all_articles(wiki_id)

        if not articles:
            await queue.put({"type": "done", "duplicates_removed": 0, "contradictions_fixed": 0, "message": "No articles to review."})
            await db.update_job_status(job_id, "done")
            return

        # ── Phase 1: Duplicates ──────────────────────────────────────────────
        await queue.put({"type": "phase", "message": f"Scanning {len(articles)} articles for duplicates..."})

        titles_str = "\n".join(f"- {a['title']}" for a in articles)
        raw = await llm.call_reasoning(
            "You are a wiki editor identifying duplicate articles.",
            FIND_DUPLICATES_PROMPT.replace("{titles}", titles_str),
        )
        groups = _parse_json_list(raw)

        duplicates_removed = 0
        for group in groups:
            if not isinstance(group, list) or len(group) < 2:
                continue
            group_articles = [a for a in articles if a["title"] in group]
            if len(group_articles) < 2:
                continue
            primary = group_articles[0]
            articles_text = "\n\n---\n\n".join(
                f"## {a['title']}\n{a['content']}" for a in group_articles
            )
            merged = await llm.call_reasoning(
                "You are a wiki editor merging duplicate articles.",
                MERGE_ARTICLES_PROMPT
                    .replace("{primary_title}", primary["title"])
                    .replace("{articles}", articles_text),
            )
            await db.upsert_article(wiki_id, primary["title"], merged)
            for dup in group_articles[1:]:
                await db.delete_article_by_title(wiki_id, dup["title"])
                await queue.put({"type": "duplicate", "title": dup["title"], "merged_into": primary["title"]})
                duplicates_removed += 1

        # Refresh after deduplication
        articles = await db.get_all_articles(wiki_id)

        # ── Phase 2: Contradictions ──────────────────────────────────────────
        await queue.put({"type": "phase", "message": f"Scanning {len(articles)} articles for contradictions..."})

        contradictions_fixed = 0
        BATCH = 12
        for i in range(0, len(articles), BATCH):
            batch = articles[i:i + BATCH]
            articles_text = "\n\n---\n\n".join(
                f"## {a['title']}\n{a['content'][:800]}" for a in batch
            )
            raw = await llm.call_reasoning(
                "You are a wiki editor checking for contradictions.",
                FIND_CONTRADICTIONS_PROMPT.replace("{articles}", articles_text),
            )
            issues = _parse_json_list(raw)
            for issue in issues:
                if not isinstance(issue, dict):
                    continue
                article = next((a for a in batch if a["title"] == issue.get("article")), None)
                if not article:
                    continue
                fixed = await llm.call_reasoning(
                    "You are a wiki editor fixing a factual error.",
                    FIX_CONTRADICTION_PROMPT
                        .replace("{title}", article["title"])
                        .replace("{content}", article["content"])
                        .replace("{issue}", issue.get("issue", ""))
                        .replace("{fix}", issue.get("fix", "")),
                )
                await db.upsert_article(wiki_id, article["title"], fixed)
                await queue.put({"type": "contradiction", "title": article["title"], "issue": issue.get("issue", "")})
                contradictions_fixed += 1

        msg = f"Done. {duplicates_removed} duplicate(s) removed, {contradictions_fixed} contradiction(s) fixed."
        await queue.put({"type": "done", "duplicates_removed": duplicates_removed, "contradictions_fixed": contradictions_fixed, "message": msg})
        await db.update_job_status(job_id, "done")

    except Exception as e:
        await queue.put({"type": "error", "message": str(e)})
        await db.update_job_status(job_id, "error", str(e))
