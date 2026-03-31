"""
Generate AI summaries and keyword tags for NYC Board of Correction meetings.

Uses Claude Haiku to summarize each meeting's content (PDFs + captions).
Stores results in the meeting_summaries table in data/boc.db.

Usage:
  python summarize.py              # summarize all meetings that have content
  python summarize.py --date 2026-01-13  # re-summarize one meeting
  python summarize.py --force      # re-run even if summary already exists
"""

import argparse
import json
import os
import sqlite3
import time
from pathlib import Path

import anthropic

DB_PATH = Path(__file__).parent / "data" / "boc.db"

# Truncation limits (chars) — keeps cost low while covering all key content
MAX_CAPTION_CHARS = 60_000   # ~15k tokens of transcript
MAX_PDF_CHARS     = 8_000    # per PDF
MAX_PDFS          = 6        # max PDFs included (agenda first, then minutes, then rest)

SYSTEM_PROMPT = """\
You are a policy analyst summarizing public meetings of the New York City Board of Correction (BOC), \
the independent oversight body for NYC jails. Your summaries will help journalists and advocates \
quickly find meetings relevant to specific issues.

Be precise and factual. Use plain language. Focus on substantive policy content, not procedural formalities.
"""

SUMMARY_PROMPT = """\
Below is content from a NYC Board of Correction public meeting on {date} ({display}).

Your task:
1. Write a SUMMARY as 3–6 bullet points, one per distinct topic or action. Each bullet should be 1–2 sentences. \
Cover: main topics discussed, any key votes or actions taken, and notable testimony or public comments. \
Be specific — name policies, rules, or programs by name where relevant. Format bullets with a leading "• " character.

2. Produce a KEYWORDS list of 8–15 topic tags covering every substantive issue touched in this meeting. \
Tags should be short phrases (1–3 words), lowercase, useful for searching across many meetings. \
Examples: "solitary confinement", "mental health", "staffing", "violence", "Rikers Island", \
"rulemaking", "medical care", "surveillance", "HALT Act", "variance", "adolescents", "women", \
"commissary", "visitation", "use of force".

Respond in this exact JSON format (no other text):
{{
  "summary": "...",
  "keywords": ["tag1", "tag2", ...]
}}

--- MEETING CONTENT ---

{content}
"""


def init_summaries_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS meeting_summaries (
            id          INTEGER PRIMARY KEY,
            meeting_id  INTEGER REFERENCES meetings(id) UNIQUE,
            summary     TEXT,
            keywords    TEXT,   -- JSON array
            model       TEXT,
            generated_at TEXT
        )
    """)
    conn.commit()


def build_content(conn, meeting_id):
    """Build a text blob for this meeting: agenda first, then other PDFs, then caption excerpt."""
    # Fetch all PDFs for this meeting, prioritize agenda > minutes > testimony > other
    TYPE_ORDER = {"agenda": 0, "minutes": 1, "report": 2, "testimony": 3, "variance": 4, "other": 5}
    docs = conn.execute("""
        SELECT title, doc_type, text FROM documents
        WHERE meeting_id = ? AND extracted = 1 AND text IS NOT NULL AND text != ''
        ORDER BY title
    """, (meeting_id,)).fetchall()
    docs.sort(key=lambda d: TYPE_ORDER.get(d[1], 9))

    sections = []
    for title, doc_type, text in docs[:MAX_PDFS]:
        truncated = text[:MAX_PDF_CHARS]
        if len(text) > MAX_PDF_CHARS:
            truncated += f"\n[... truncated at {MAX_PDF_CHARS} chars ...]"
        sections.append(f"### {doc_type.upper()}: {title}\n{truncated}")

    # Append caption excerpt
    caption = conn.execute(
        "SELECT text FROM captions WHERE meeting_id = ? AND text IS NOT NULL",
        (meeting_id,)
    ).fetchone()
    if caption and caption[0]:
        cap_text = caption[0][:MAX_CAPTION_CHARS]
        if len(caption[0]) > MAX_CAPTION_CHARS:
            cap_text += f"\n[... transcript truncated at {MAX_CAPTION_CHARS} chars ...]"
        sections.append(f"### MEETING TRANSCRIPT (auto-captions)\n{cap_text}")

    return "\n\n".join(sections)


def summarize_meeting(client, meeting_id, date, display, content):
    """Call Claude and return (summary, keywords_list)."""
    prompt = SUMMARY_PROMPT.format(date=date, display=display, content=content)

    msg = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    )

    text = msg.content[0].text.strip()
    # Strip any markdown code fences if present
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    result = json.loads(text)
    return result["summary"], result["keywords"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", help="Summarize a single meeting by date (YYYY-MM-DD)")
    parser.add_argument("--force", action="store_true", help="Re-run even if summary exists")
    args = parser.parse_args()

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("Set ANTHROPIC_API_KEY environment variable")

    client = anthropic.Anthropic(api_key=api_key)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    init_summaries_table(conn)

    # Get meetings that have content
    if args.date:
        meetings = conn.execute(
            "SELECT id, date, display FROM meetings WHERE date = ?", (args.date,)
        ).fetchall()
    else:
        # Only meetings that have at least one document or a caption
        meetings = conn.execute("""
            SELECT DISTINCT m.id, m.date, m.display
            FROM meetings m
            WHERE EXISTS (SELECT 1 FROM documents d WHERE d.meeting_id = m.id AND d.extracted = 1)
               OR EXISTS (SELECT 1 FROM captions c WHERE c.meeting_id = m.id AND c.text IS NOT NULL)
            ORDER BY m.date
        """).fetchall()

    print(f"Found {len(meetings)} meetings with content\n")

    for mtg in meetings:
        mid, date, display = mtg["id"], mtg["date"], mtg["display"]

        if not args.force:
            existing = conn.execute(
                "SELECT id FROM meeting_summaries WHERE meeting_id = ?", (mid,)
            ).fetchone()
            if existing:
                print(f"  {date} — already summarized, skipping (use --force to re-run)")
                continue

        print(f"  {date} — {display}...")
        content = build_content(conn, mid)
        if not content.strip():
            print(f"    [no content, skipping]")
            continue

        try:
            summary, keywords = summarize_meeting(client, mid, date, display, content)
            conn.execute("""
                INSERT INTO meeting_summaries (meeting_id, summary, keywords, model, generated_at)
                VALUES (?, ?, ?, ?, datetime('now'))
                ON CONFLICT(meeting_id) DO UPDATE SET
                    summary = excluded.summary,
                    keywords = excluded.keywords,
                    model = excluded.model,
                    generated_at = excluded.generated_at
            """, (mid, summary, json.dumps(keywords), "claude-haiku-4-5-20251001"))
            conn.commit()
            print(f"    Summary: {summary[:100]}...")
            print(f"    Keywords: {', '.join(keywords[:6])}{'...' if len(keywords) > 6 else ''}")
            time.sleep(0.5)  # gentle rate limit
        except Exception as e:
            print(f"    [error: {e}]")

    conn.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
