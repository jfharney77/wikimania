"""Build the wikimania PowerPoint slide deck as specified in POWERPOINT_SPEC.md."""

from pptx import Presentation
from pptx.util import Inches, Pt, Emu
from pptx.enum.text import PP_ALIGN
from pptx.dml.color import RGBColor
from pptx.util import Inches, Pt

# ── Palette ──────────────────────────────────────────────────────────────────
BG      = RGBColor(0x0D, 0x1B, 0x2A)   # deep navy
ACCENT  = RGBColor(0x00, 0xC2, 0xA8)   # teal
WHITE   = RGBColor(0xFF, 0xFF, 0xFF)
LIGHT   = RGBColor(0xCC, 0xE5, 0xFF)
MUTED   = RGBColor(0x88, 0xAA, 0xCC)
CODE_BG = RGBColor(0x16, 0x28, 0x3A)

SLIDE_W = Inches(13.333)
SLIDE_H = Inches(7.5)


def set_bg(slide, color: RGBColor):
    from pptx.oxml.ns import qn
    from lxml import etree
    bg = slide.background
    fill = bg.fill
    fill.solid()
    fill.fore_color.rgb = color


def add_textbox(slide, left, top, width, height, text, size=18,
                bold=False, color=WHITE, align=PP_ALIGN.LEFT, wrap=True):
    txBox = slide.shapes.add_textbox(left, top, width, height)
    txBox.word_wrap = wrap
    tf = txBox.text_frame
    tf.word_wrap = wrap
    p = tf.paragraphs[0]
    p.alignment = align
    run = p.add_run()
    run.text = text
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.color.rgb = color
    return txBox


def add_title_bar(slide, title_text, subtitle_text=None):
    """Teal top bar with title."""
    bar = slide.shapes.add_shape(
        1,  # MSO_SHAPE_TYPE.RECTANGLE
        0, 0, SLIDE_W, Inches(1.4)
    )
    bar.fill.solid()
    bar.fill.fore_color.rgb = ACCENT
    bar.line.fill.background()

    add_textbox(slide, Inches(0.4), Inches(0.12), Inches(12.5), Inches(0.9),
                title_text, size=32, bold=True, color=BG, align=PP_ALIGN.LEFT)

    if subtitle_text:
        add_textbox(slide, Inches(0.4), Inches(1.45), Inches(12.5), Inches(0.5),
                    subtitle_text, size=16, color=MUTED)


def add_bullet(slide, left, top, width, height, items, size=18, indent=False):
    txBox = slide.shapes.add_textbox(left, top, width, height)
    txBox.word_wrap = True
    tf = txBox.text_frame
    tf.word_wrap = True
    first = True
    for item in items:
        if first:
            p = tf.paragraphs[0]
            first = False
        else:
            p = tf.add_paragraph()
        p.space_before = Pt(4)
        run = p.add_run()
        bullet = "    •  " if indent else "•  "
        run.text = bullet + item
        run.font.size = Pt(size)
        run.font.color.rgb = LIGHT


def add_code_block(slide, left, top, width, height, code_text, font_size=14):
    box = slide.shapes.add_shape(1, left, top, width, height)
    box.fill.solid()
    box.fill.fore_color.rgb = CODE_BG
    box.line.color.rgb = ACCENT

    txBox = slide.shapes.add_textbox(
        left + Inches(0.15), top + Inches(0.1),
        width - Inches(0.3), height - Inches(0.2)
    )
    txBox.word_wrap = False
    tf = txBox.text_frame
    tf.word_wrap = False
    p = tf.paragraphs[0]
    run = p.add_run()
    run.text = code_text
    run.font.size = Pt(font_size)
    run.font.color.rgb = ACCENT
    run.font.name = "Courier New"


# ── Slides ────────────────────────────────────────────────────────────────────

def slide_01_title(prs):
    """Slide 1 — Title."""
    slide = prs.slides.add_slide(prs.slide_layouts[6])  # blank
    set_bg(slide, BG)

    # big teal stripe
    stripe = slide.shapes.add_shape(1, 0, Inches(2.2), SLIDE_W, Inches(3.1))
    stripe.fill.solid()
    stripe.fill.fore_color.rgb = RGBColor(0x07, 0x22, 0x38)
    stripe.line.fill.background()

    add_textbox(slide, Inches(1.0), Inches(2.3), Inches(11.3), Inches(1.2),
                "wikimania", size=64, bold=True, color=ACCENT, align=PP_ALIGN.CENTER)

    add_textbox(slide, Inches(1.0), Inches(3.55), Inches(11.3), Inches(0.8),
                "Turn uploaded documents into a linked, queryable wiki.",
                size=24, color=WHITE, align=PP_ALIGN.CENTER)

    add_textbox(slide, Inches(1.0), Inches(4.55), Inches(11.3), Inches(0.5),
                "Upload  →  Extract  →  Graph  →  Query",
                size=18, color=MUTED, align=PP_ALIGN.CENTER)

    add_textbox(slide, Inches(0.4), Inches(6.8), Inches(12.5), Inches(0.4),
                "FastAPI + React (Vite)  ·  Groq LLMs  ·  graphify  ·  PostgreSQL",
                size=13, color=MUTED, align=PP_ALIGN.CENTER)


def slide_02_what(prs):
    """Slide 2 — What it is."""
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    set_bg(slide, BG)
    add_title_bar(slide, "What is wikimania?")

    add_bullet(slide, Inches(0.6), Inches(1.7), Inches(7.5), Inches(4.5), [
        "Upload any Markdown document",
        "Automatically generates a wiki of interlinked articles",
        "Browse articles with clickable [[wikilinks]]",
        "Explore the knowledge graph visually",
        "Ask natural-language questions — get LLM-synthesised answers",
    ], size=19)

    # Four tabs box
    box = slide.shapes.add_shape(1, Inches(8.4), Inches(1.8), Inches(4.5), Inches(4.0))
    box.fill.solid()
    box.fill.fore_color.rgb = RGBColor(0x07, 0x22, 0x38)
    box.line.color.rgb = ACCENT

    add_textbox(slide, Inches(8.55), Inches(1.9), Inches(4.2), Inches(0.5),
                "Four tabs", size=15, bold=True, color=ACCENT)

    tabs = ["Upload", "Wiki", "Graph", "Query"]
    for i, t in enumerate(tabs):
        add_textbox(slide, Inches(8.7), Inches(2.4 + i * 0.72), Inches(3.8), Inches(0.6),
                    f"[{i+1}]  {t}", size=17, color=WHITE)


def slide_03_arch(prs):
    """Slide 3 — Architecture."""
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    set_bg(slide, BG)
    add_title_bar(slide, "Architecture")

    # Two columns
    labels = [
        ("Frontend", "React 18 + Vite (SPA)\nfour-tab UI\nVite dev-server proxies /api/*\n→ no CORS friction"),
        ("Backend", "FastAPI (Python)\nmain.py  —  all API endpoints\npipeline.py  —  wiki generation\nPostgreSQL  —  durable state"),
    ]
    for i, (head, body) in enumerate(labels):
        bx = Inches(0.5 + i * 6.5)
        box = slide.shapes.add_shape(1, bx, Inches(1.65), Inches(5.9), Inches(4.2))
        box.fill.solid()
        box.fill.fore_color.rgb = RGBColor(0x07, 0x22, 0x38)
        box.line.color.rgb = ACCENT

        add_textbox(slide, bx + Inches(0.2), Inches(1.8), Inches(5.5), Inches(0.5),
                    head, size=20, bold=True, color=ACCENT)
        add_textbox(slide, bx + Inches(0.2), Inches(2.35), Inches(5.5), Inches(3.2),
                    body, size=16, color=LIGHT)

    add_textbox(slide, Inches(6.2), Inches(3.6), Inches(0.9), Inches(0.5),
                "⟷", size=28, bold=True, color=ACCENT, align=PP_ALIGN.CENTER)


def slide_04_async(prs):
    """Slide 4 — Async generation."""
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    set_bg(slide, BG)
    add_title_bar(slide, "Async Generation via SSE")

    add_bullet(slide, Inches(0.6), Inches(1.7), Inches(6.5), Inches(2.5), [
        "POST /api/documents/upload → returns job_id immediately",
        "Background worker runs pipeline.py",
        "Frontend streams progress via Server-Sent Events",
        "Events survive restarts — stored in job_events table",
    ], size=18)

    add_code_block(slide, Inches(0.6), Inches(3.9), Inches(5.8), Inches(2.8),
                   "SSE event types:\n  phase       — pipeline phase started\n  concepts    — extracted concept list\n  article     — article written/updated\n  graph_done  — graphify rebuild done\n  done        — job finished\n  error       — job failed\n  heartbeat   — keep-alive (25 s)")

    # Flow diagram (right side)
    steps = ["Upload .md", "job_id ↩", "SSE stream", "Progress UI"]
    for i, s in enumerate(steps):
        bx = slide.shapes.add_shape(1, Inches(7.5), Inches(1.8 + i * 1.2), Inches(4.8), Inches(0.8))
        bx.fill.solid()
        bx.fill.fore_color.rgb = RGBColor(0x07, 0x22, 0x38)
        bx.line.color.rgb = ACCENT
        add_textbox(slide, Inches(7.6), Inches(1.86 + i * 1.2), Inches(4.5), Inches(0.6),
                    s, size=17, color=WHITE)
        if i < 3:
            add_textbox(slide, Inches(9.4), Inches(2.6 + i * 1.2), Inches(1.0), Inches(0.4),
                        "↓", size=18, bold=True, color=ACCENT, align=PP_ALIGN.CENTER)


def slide_05_llm(prs):
    """Slide 5 — Two LLM tiers."""
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    set_bg(slide, BG)
    add_title_bar(slide, "Two LLM Tiers (via Groq)")

    tiers = [
        ("MODEL_FAST", "llama-3.1-8b-instant", "Concept extraction\nQuick pass over each chunk\nLow latency, high throughput"),
        ("MODEL_REASONING", "qwen/qwen3-32b", "Article writing\nQuery answering\nDeeper reasoning capability"),
    ]
    for i, (env, model, desc) in enumerate(tiers):
        bx = Inches(0.5 + i * 6.5)
        box = slide.shapes.add_shape(1, bx, Inches(1.65), Inches(5.9), Inches(4.5))
        box.fill.solid()
        box.fill.fore_color.rgb = RGBColor(0x07, 0x22, 0x38)
        box.line.color.rgb = ACCENT

        add_textbox(slide, bx + Inches(0.2), Inches(1.8), Inches(5.5), Inches(0.55),
                    env, size=19, bold=True, color=ACCENT)
        add_textbox(slide, bx + Inches(0.2), Inches(2.4), Inches(5.5), Inches(0.55),
                    model, size=16, color=WHITE)
        add_textbox(slide, bx + Inches(0.2), Inches(3.05), Inches(5.5), Inches(2.5),
                    desc, size=16, color=LIGHT)

    add_textbox(slide, Inches(0.5), Inches(6.25), Inches(12.3), Inches(0.45),
                "Both tiers configurable via .env — swap Groq for Ollama with PROVIDER=ollama",
                size=14, color=MUTED)


def slide_06_graph(prs):
    """Slide 6 — Knowledge graph."""
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    set_bg(slide, BG)
    add_title_bar(slide, "Knowledge Graph — graphify")

    add_bullet(slide, Inches(0.6), Inches(1.7), Inches(7.5), Inches(3.5), [
        "graphify used as a Python library (not subprocess)",
        "build()  →  cluster()  →  export()  called in pipeline.py",
        "Graph JSON stored in graph_snapshots (Postgres)",
        "Frontend fetches GET /api/wiki/graph and renders interactively",
        "Rebuilt automatically after every document upload",
    ], size=19)

    add_code_block(slide, Inches(0.6), Inches(5.0), Inches(7.3), Inches(1.75),
                   "from graphify import build, cluster, export\n\ngraph = build(articles)\ngraph = cluster(graph)\njson_out = export(graph)")

    # mini legend
    box = slide.shapes.add_shape(1, Inches(8.4), Inches(1.8), Inches(4.5), Inches(3.8))
    box.fill.solid()
    box.fill.fore_color.rgb = RGBColor(0x07, 0x22, 0x38)
    box.line.color.rgb = ACCENT
    add_textbox(slide, Inches(8.55), Inches(1.9), Inches(4.2), Inches(0.5),
                "graph_snapshots table", size=14, bold=True, color=ACCENT)
    add_textbox(slide, Inches(8.55), Inches(2.4), Inches(4.2), Inches(2.8),
                "id\ncreated_at\ndocument_id\ngraph_json  (JSONB)\nnodes / edges count",
                size=14, color=LIGHT)


def slide_07_obsidian(prs):
    """Slide 7 — Obsidian-compatible."""
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    set_bg(slide, BG)
    add_title_bar(slide, "Obsidian-Compatible Export")

    add_bullet(slide, Inches(0.6), Inches(1.7), Inches(7.5), Inches(3.2), [
        "Articles stored as Markdown with [[wikilink]] syntax",
        "article_links table tracks parsed wikilink edges",
        "GET /api/wiki/export zips all articles as an Obsidian vault",
        "Open the .zip in Obsidian → full graph view immediately",
        "No vendor lock-in: plain .md files",
    ], size=19)

    add_code_block(slide, Inches(0.6), Inches(4.85), Inches(7.3), Inches(1.85),
                   "## Quantum Entanglement\n\nEntanglement is described in [[Bell's theorem]].\nSee also [[EPR paradox]] and [[Superposition]].\n\n...")

    box = slide.shapes.add_shape(1, Inches(8.4), Inches(1.8), Inches(4.5), Inches(3.5))
    box.fill.solid()
    box.fill.fore_color.rgb = RGBColor(0x07, 0x22, 0x38)
    box.line.color.rgb = ACCENT
    add_textbox(slide, Inches(8.55), Inches(1.9), Inches(4.2), Inches(0.5),
                "article_links table", size=14, bold=True, color=ACCENT)
    add_textbox(slide, Inches(8.55), Inches(2.45), Inches(4.2), Inches(2.5),
                "from_article_id  (FK)\nto_title  (text)\n\n→ Used to build the\n   knowledge graph edges",
                size=15, color=LIGHT)


def slide_08_query(prs):
    """Slide 8 — Query."""
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    set_bg(slide, BG)
    add_title_bar(slide, "Query — No Embeddings Needed")

    add_bullet(slide, Inches(0.6), Inches(1.7), Inches(7.0), Inches(3.2), [
        "No vector store, no embeddings infrastructure",
        "Postgres ILIKE search finds relevant articles",
        "Matched articles passed as context to MODEL_REASONING",
        "LLM synthesises a cited, grounded answer",
        "POST /api/wiki/query  →  {answer, sources}",
    ], size=19)

    add_code_block(slide, Inches(0.6), Inches(4.85), Inches(7.3), Inches(1.85),
                   "SELECT * FROM wiki_articles\nWHERE content ILIKE '%{term}%'\nLIMIT 10;\n\n→ feed to MODEL_REASONING → answer")

    box = slide.shapes.add_shape(1, Inches(8.2), Inches(1.8), Inches(4.7), Inches(4.5))
    box.fill.solid()
    box.fill.fore_color.rgb = RGBColor(0x07, 0x22, 0x38)
    box.line.color.rgb = ACCENT
    add_textbox(slide, Inches(8.35), Inches(1.9), Inches(4.4), Inches(0.5),
                "Why this works", size=15, bold=True, color=ACCENT)
    add_textbox(slide, Inches(8.35), Inches(2.45), Inches(4.4), Inches(3.5),
                "Docs are already chunked\ninto focused articles.\n\nEach article ≈ one concept\n→ ILIKE precision is high\n→ context window stays small\n→ answer quality stays high",
                size=15, color=LIGHT)


def slide_09_deploy(prs):
    """Slide 9 — Deploy."""
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    set_bg(slide, BG)
    add_title_bar(slide, "Deployment")

    add_bullet(slide, Inches(0.6), Inches(1.7), Inches(7.3), Inches(2.8), [
        "Full deployment guide in AWS_DEPLOYMENT.md / DEPLOYMENT.md",
        "Backend: FastAPI on ECS Fargate (or any Python host)",
        "Frontend: Vite build → S3 + CloudFront (or Railway/Vercel)",
        "Database: RDS PostgreSQL (or any Postgres)",
        "Secrets via environment variables (.env or AWS Secrets Manager)",
    ], size=18)

    add_code_block(slide, Inches(0.6), Inches(4.55), Inches(7.3), Inches(2.15),
                   "# .env (backend)\nGROQ_API_KEY=gsk_...\nDATABASE_URL=postgresql://...\nPROVIDER=groq\nMODEL_FAST=llama-3.1-8b-instant\nMODEL_REASONING=qwen/qwen3-32b")

    box = slide.shapes.add_shape(1, Inches(8.2), Inches(1.8), Inches(4.7), Inches(4.7))
    box.fill.solid()
    box.fill.fore_color.rgb = RGBColor(0x07, 0x22, 0x38)
    box.line.color.rgb = ACCENT
    add_textbox(slide, Inches(8.35), Inches(1.9), Inches(4.4), Inches(0.5),
                "Local dev", size=15, bold=True, color=ACCENT)
    add_textbox(slide, Inches(8.35), Inches(2.45), Inches(4.4), Inches(3.8),
                "cd backend\npython3 -m venv .venv\n.venv/bin/pip install -r requirements.txt\n.venv/bin/uvicorn main:app --reload\n\ncd frontend\nnpm install && npm run dev\n\nOpen http://localhost:5173",
                size=13, color=LIGHT)


def slide_10_demo(prs):
    """Slide 10 — Demo + closing."""
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    set_bg(slide, BG)
    add_title_bar(slide, "Demo & Next Steps")

    add_bullet(slide, Inches(0.6), Inches(1.7), Inches(7.5), Inches(3.5), [
        "Upload a sample Markdown document",
        "Watch SSE progress stream fill in articles",
        "Browse the wiki — click [[wikilinks]] to navigate",
        "Open the Graph tab — explore the knowledge cluster",
        "Ask a question in the Query tab",
        "Export → open vault in Obsidian",
    ], size=19)

    box = slide.shapes.add_shape(1, Inches(8.2), Inches(1.8), Inches(4.7), Inches(4.0))
    box.fill.solid()
    box.fill.fore_color.rgb = RGBColor(0x07, 0x22, 0x38)
    box.line.color.rgb = ACCENT
    add_textbox(slide, Inches(8.35), Inches(1.9), Inches(4.4), Inches(0.5),
                "What's next?", size=16, bold=True, color=ACCENT)
    add_bullet(slide, Inches(8.35), Inches(2.45), Inches(4.4), Inches(3.0), [
        "Embeddings + semantic search",
        "Multi-document cross-linking",
        "Incremental graph updates",
        "Collaboration / shared wikis",
        "slides2video narration",
    ], size=15)

    add_textbox(slide, Inches(0.5), Inches(6.7), Inches(12.3), Inches(0.5),
                "github.com/your-org/wikimania  ·  jfharney@gmail.com",
                size=13, color=MUTED, align=PP_ALIGN.CENTER)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    prs = Presentation()
    prs.slide_width = SLIDE_W
    prs.slide_height = SLIDE_H

    slide_01_title(prs)
    slide_02_what(prs)
    slide_03_arch(prs)
    slide_04_async(prs)
    slide_05_llm(prs)
    slide_06_graph(prs)
    slide_07_obsidian(prs)
    slide_08_query(prs)
    slide_09_deploy(prs)
    slide_10_demo(prs)

    out = "wikimania.pptx"
    prs.save(out)
    print(f"Saved {out}  ({len(prs.slides)} slides)")


if __name__ == "__main__":
    main()
