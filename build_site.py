"""
Build the NYC BOC viewer site.
Reads boc.db (including meeting_summaries) and writes docs/index.html.

Run: python build_site.py
"""

import json
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "boc.db"
DOCS_DIR = Path(__file__).parent / "docs"
OUT_HTML = DOCS_DIR / "index.html"


def main():
    DOCS_DIR.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # Load summaries (may not exist if summarize.py hasn't been run)
    summaries = {}
    try:
        for row in conn.execute("SELECT meeting_id, summary, keywords FROM meeting_summaries"):
            summaries[row["meeting_id"]] = {
                "summary": row["summary"],
                "keywords": json.loads(row["keywords"] or "[]"),
            }
    except Exception:
        pass  # table doesn't exist yet

    meetings = []
    for m in conn.execute("SELECT * FROM meetings ORDER BY date DESC"):
        docs = []
        for d in conn.execute(
            "SELECT id, title, url, doc_type, extracted FROM documents WHERE meeting_id=? ORDER BY doc_type, title",
            (m["id"],),
        ):
            docs.append({
                "id": d["id"],
                "title": d["title"],
                "url": d["url"],
                "doc_type": d["doc_type"],
                "has_text": bool(d["extracted"]),
            })

        cap = conn.execute(
            "SELECT source, text FROM captions WHERE meeting_id=?", (m["id"],)
        ).fetchone()
        caption_text = (cap["text"] if cap and cap["text"] else "")

        sm = summaries.get(m["id"], {})

        meetings.append({
            "date": m["date"],
            "display": m["display"] or m["date"],
            "page_url": m["page_url"],
            "youtube_id": m["youtube_id"],
            "youtube_url": m["youtube_url"],
            "docs": docs,
            "has_caption": bool(caption_text),
            "caption_text": caption_text,
            "summary": sm.get("summary", ""),
            "keywords": sm.get("keywords", []),
        })

    # Per-document text for search and panel
    doc_texts = {}
    for row in conn.execute("SELECT id, text FROM documents WHERE extracted=1 AND text IS NOT NULL"):
        doc_texts[row["id"]] = row["text"]

    conn.close()

    data = {"meetings": meetings, "doc_texts": doc_texts}
    data_js = "const DATA = " + json.dumps(data, ensure_ascii=False, separators=(",", ":")) + ";"

    html = build_html(data_js)
    OUT_HTML.write_text(html, encoding="utf-8")
    print(f"Wrote {OUT_HTML}")
    n_with_summary = sum(1 for m in meetings if m["summary"])
    print(f"  {len(meetings)} meetings · {sum(len(m['docs']) for m in meetings)} docs · {n_with_summary} with AI summaries")


def build_html(data_js):
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>NYC Board of Correction — Meeting Archive</title>
<style>
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ font-family: Georgia, serif; background: #f5f2ed; color: #222; font-size: 15px; line-height: 1.5; }}
a {{ color: #326891; text-decoration: none; }}
a:hover {{ text-decoration: underline; }}

header {{
  background: #1a3a5c;
  color: #fff;
  padding: 16px 28px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
}}
header h1 {{ font-size: 19px; font-weight: normal; letter-spacing: 0.02em; }}
header .subtitle {{ font-size: 11px; opacity: 0.7; margin-top: 2px; }}

.search-bar {{
  display: flex; gap: 6px; align-items: center; flex-wrap: wrap;
}}
.search-bar input {{
  padding: 6px 12px; font-size: 14px; border: none; border-radius: 4px;
  width: 280px; font-family: inherit;
}}
.search-bar select {{
  padding: 6px 8px; font-size: 13px; border: none; border-radius: 4px;
  font-family: inherit; background: #fff;
}}
.search-bar button {{
  padding: 6px 14px; font-size: 13px; background: #e8a020; color: #fff;
  border: none; border-radius: 4px; cursor: pointer; font-family: inherit;
}}
.search-bar button:hover {{ background: #c88010; }}

.container {{ max-width: 1000px; margin: 0 auto; padding: 20px 20px; }}

/* Keyword filter bar */
#keywordBar {{
  margin-bottom: 16px;
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: center;
}}
#keywordBar .bar-label {{
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: #888;
  font-family: sans-serif;
  margin-right: 4px;
  white-space: nowrap;
}}
.kw-filter {{
  padding: 3px 10px;
  background: #e8eef4;
  color: #1a3a5c;
  border: 1px solid #b8cfe0;
  border-radius: 12px;
  font-size: 12px;
  font-family: sans-serif;
  cursor: pointer;
  white-space: nowrap;
  transition: background 0.15s;
}}
.kw-filter:hover {{ background: #cddceb; }}
.kw-filter.active {{ background: #1a3a5c; color: #fff; border-color: #1a3a5c; }}

/* Search results */
#searchResults {{ margin-bottom: 16px; }}
.search-hit {{
  background: #fff;
  border-left: 3px solid #326891;
  border-radius: 0 4px 4px 0;
  padding: 10px 14px;
  margin-bottom: 8px;
  cursor: pointer;
}}
.search-hit:hover {{ background: #eef4f8; }}
.search-hit .hit-date {{ font-size: 11px; color: #888; font-family: sans-serif; }}
.search-hit .hit-title {{ font-weight: bold; font-size: 14px; margin: 2px 0 3px; }}
.search-hit .hit-snippet {{ font-size: 13px; color: #444; font-family: sans-serif; }}
.search-hit .hit-snippet mark {{ background: #ffe066; padding: 0 1px; }}
.no-results {{ color: #888; font-style: italic; padding: 12px 0; font-family: sans-serif; }}

/* Meeting rows */
.meeting-row {{
  background: #fff;
  border-radius: 6px;
  box-shadow: 0 1px 3px rgba(0,0,0,0.07);
  margin-bottom: 10px;
  overflow: hidden;
}}
.meeting-row.no-content {{
  opacity: 0.55;
}}
.row-head {{
  display: flex;
  align-items: baseline;
  gap: 12px;
  padding: 12px 16px 8px;
  flex-wrap: wrap;
}}
.row-date {{
  font-size: 16px;
  font-weight: bold;
  color: #1a3a5c;
  white-space: nowrap;
}}
.row-year {{
  font-size: 12px;
  color: #aaa;
  font-family: sans-serif;
}}
.row-yt {{
  margin-left: auto;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  background: #c00;
  color: #fff;
  padding: 3px 9px;
  border-radius: 4px;
  font-size: 11px;
  font-family: sans-serif;
  white-space: nowrap;
}}
.row-yt:hover {{ background: #a00; text-decoration: none; }}

.row-summary {{
  padding: 0 16px 8px;
  font-size: 14px;
  color: #333;
  line-height: 1.55;
}}
.row-summary em {{
  font-style: normal;
  color: #888;
  font-family: sans-serif;
  font-size: 13px;
}}

.row-keywords {{
  padding: 0 16px 8px;
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
}}
.kw-chip {{
  padding: 2px 8px;
  background: #eef4f8;
  color: #326891;
  border-radius: 10px;
  font-size: 11px;
  font-family: sans-serif;
  cursor: pointer;
  white-space: nowrap;
  transition: background 0.15s;
}}
.kw-chip:hover {{ background: #cddceb; }}
.kw-chip.active {{ background: #1a3a5c; color: #fff; }}

.row-docs {{
  padding: 6px 16px 12px;
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
  border-top: 1px solid #f0ece4;
}}
.doc-chip {{
  display: inline-flex;
  align-items: center;
  gap: 3px;
  padding: 3px 9px;
  border-radius: 10px;
  font-size: 12px;
  font-family: sans-serif;
  cursor: pointer;
  border: 1px solid transparent;
  transition: opacity 0.15s;
}}
.doc-chip.minutes   {{ background: #ddeeff; color: #1a3a5c; border-color: #b0d0f0; }}
.doc-chip.agenda    {{ background: #e8f4e8; color: #1a4a1a; border-color: #a0d0a0; }}
.doc-chip.testimony {{ background: #fff3dd; color: #5a3a00; border-color: #e0c080; }}
.doc-chip.variance  {{ background: #f5e8f5; color: #4a1a4a; border-color: #d0a0d0; }}
.doc-chip.report    {{ background: #f0f0f0; color: #333; border-color: #ccc; }}
.doc-chip.other     {{ background: #f5f5f5; color: #555; border-color: #ddd; }}
.doc-chip:hover {{ opacity: 0.75; }}
.cap-chip {{
  display: inline-flex;
  align-items: center;
  gap: 3px;
  padding: 3px 9px;
  border-radius: 10px;
  font-size: 12px;
  font-family: sans-serif;
  cursor: pointer;
  background: #fff3e0;
  color: #6b3800;
  border: 1px solid #e8c080;
}}
.cap-chip:hover {{ opacity: 0.75; }}

/* Detail panel */
#detailPanel {{
  position: fixed;
  top: 0; right: 0;
  width: 52vw; max-width: 780px; min-width: 340px;
  height: 100vh;
  background: #fff;
  box-shadow: -4px 0 20px rgba(0,0,0,0.15);
  display: flex; flex-direction: column;
  z-index: 100;
  transform: translateX(100%);
  transition: transform 0.22s ease;
}}
#detailPanel.open {{ transform: translateX(0); }}
.panel-header {{
  background: #1a3a5c; color: #fff;
  padding: 12px 16px;
  display: flex; align-items: flex-start; gap: 10px;
}}
.panel-header h3 {{ flex: 1; font-size: 15px; font-weight: bold; line-height: 1.3; }}
.panel-close {{
  background: none; border: none; color: #fff;
  font-size: 20px; cursor: pointer; line-height: 1; opacity: 0.7; padding: 0 4px;
  flex-shrink: 0;
}}
.panel-close:hover {{ opacity: 1; }}
.panel-meta {{ padding: 8px 16px; background: #f0ece0; font-size: 12px; color: #666; font-family: sans-serif; }}
.panel-meta a {{ color: #326891; }}
.panel-search {{
  padding: 8px 16px; background: #f8f8f8; border-bottom: 1px solid #eee;
}}
.panel-search input {{
  width: 100%; padding: 5px 10px; font-size: 13px;
  border: 1px solid #ddd; border-radius: 4px; font-family: sans-serif;
}}
.panel-body {{
  flex: 1; overflow-y: auto; padding: 14px 16px;
  font-family: sans-serif; font-size: 13px; line-height: 1.6;
  color: #333; white-space: pre-wrap; word-wrap: break-word;
}}
.panel-body mark {{ background: #ffe066; }}
#overlay {{
  display: none; position: fixed; inset: 0;
  background: rgba(0,0,0,0.25); z-index: 99;
}}
#overlay.open {{ display: block; }}

@media (max-width: 700px) {{
  #detailPanel {{ width: 100vw; max-width: 100vw; }}
  .search-bar input {{ width: 100%; }}
  header {{ flex-direction: column; align-items: flex-start; }}
}}
</style>
</head>
<body>

<header>
  <div>
    <h1>NYC Board of Correction</h1>
    <div class="subtitle">Meeting Archive &mdash; Summaries, Documents &amp; Transcripts</div>
  </div>
  <div class="search-bar">
    <input type="text" id="searchInput" placeholder="Search all meeting content..." autocomplete="off">
    <select id="searchScope">
      <option value="all">All content</option>
      <option value="minutes">Minutes</option>
      <option value="testimony">Testimony</option>
      <option value="captions">Transcripts</option>
    </select>
    <button onclick="runSearch()">Search</button>
  </div>
</header>

<div id="overlay" onclick="closePanel()"></div>

<div id="detailPanel">
  <div class="panel-header">
    <h3 id="panelTitle"></h3>
    <button class="panel-close" onclick="closePanel()">&#x2715;</button>
  </div>
  <div class="panel-meta" id="panelMeta"></div>
  <div class="panel-search">
    <input type="text" id="panelSearch" placeholder="Find in document..." oninput="highlightInPanel(this.value)">
  </div>
  <div class="panel-body" id="panelBody"></div>
</div>

<div class="container">
  <div id="keywordBar"></div>
  <div id="searchResults"></div>
  <div id="meetingsList"></div>
</div>

<script>
{data_js}

var currentQuery = "";
var activeKeyword = "";

function escHtml(s) {{
  return (s||"").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
}}
function highlight(text, query) {{
  if (!query) return escHtml(text);
  var re = new RegExp("(" + query.replace(/[.*+?^${{}}()|[\\]\\\\]/g,"\\\\$&") + ")", "gi");
  return escHtml(text).replace(re, "<mark>$1</mark>");
}}
function snippet(text, query, radius) {{
  radius = radius || 140;
  if (!query || !text) return escHtml((text||"").slice(0,180));
  var idx = text.toLowerCase().indexOf(query.toLowerCase());
  if (idx < 0) return escHtml(text.slice(0,180));
  var start = Math.max(0, idx - radius);
  var end = Math.min(text.length, idx + query.length + radius);
  var chunk = (start > 0 ? "…" : "") + text.slice(start, end) + (end < text.length ? "…" : "");
  return highlight(chunk, query);
}}

// ── Keyword filter bar ────────────────────────────────────────────

function buildKeywordBar() {{
  var freq = {{}};
  DATA.meetings.forEach(function(m) {{
    (m.keywords||[]).forEach(function(k) {{ freq[k] = (freq[k]||0) + 1; }});
  }});
  var sorted = Object.keys(freq).sort(function(a,b) {{ return freq[b]-freq[a]; }});
  var bar = document.getElementById("keywordBar");
  if (!sorted.length) {{ bar.style.display = "none"; return; }}
  var html = '<span class="bar-label">Filter by topic:</span>';
  sorted.slice(0, 30).forEach(function(k) {{
    html += '<span class="kw-filter" data-kw="' + escHtml(k) + '">' + escHtml(k) + '</span>';
  }});
  bar.innerHTML = html;
  bar.querySelectorAll(".kw-filter").forEach(function(el) {{
    el.addEventListener("click", function() {{
      var kw = this.dataset.kw;
      if (activeKeyword === kw) {{
        activeKeyword = "";
        this.classList.remove("active");
      }} else {{
        document.querySelectorAll(".kw-filter.active").forEach(function(x){{x.classList.remove("active");}});
        activeKeyword = kw;
        this.classList.add("active");
      }}
      renderMeetings();
    }});
  }});
}}

// ── Search ───────────────────────────────────────────────────────

function runSearch() {{
  currentQuery = document.getElementById("searchInput").value.trim();
  var scope = document.getElementById("searchScope").value;
  var container = document.getElementById("searchResults");
  if (!currentQuery) {{
    container.innerHTML = "";
    renderMeetings();
    return;
  }}
  var q = currentQuery.toLowerCase();
  var hits = [];

  DATA.meetings.forEach(function(m) {{
    if (scope === "all" || scope === "captions") {{
      if (m.has_caption && m.caption_text && m.caption_text.toLowerCase().indexOf(q) >= 0) {{
        hits.push({{ date: m.date, display: m.display, title: "Video Transcript", source: "transcript",
          text: m.caption_text, action: (function(x){{ return function(){{ openCaption(x); }}; }})(m) }});
      }}
    }}
    m.docs.forEach(function(d) {{
      if (scope !== "all" && scope !== d.doc_type) return;
      var text = DATA.doc_texts[d.id] || "";
      if (text && text.toLowerCase().indexOf(q) >= 0) {{
        hits.push({{ date: m.date, display: m.display, title: d.title, source: d.doc_type,
          text: text, url: d.url, action: (function(dd, tt){{ return function(){{ openDoc(dd, tt); }}; }})(d, text) }});
      }}
    }});
  }});

  hits.sort(function(a,b) {{ return b.date < a.date ? -1 : 1; }});

  if (!hits.length) {{
    container.innerHTML = '<div class="no-results">No results for &ldquo;' + escHtml(currentQuery) + '&rdquo;</div>';
    document.getElementById("meetingsList").style.display = "";
    return;
  }}
  var html = '<div style="font-size:13px;color:#666;font-family:sans-serif;margin-bottom:10px">'
    + hits.length + ' result' + (hits.length===1?'':'s') + ' for &ldquo;' + escHtml(currentQuery) + '&rdquo;</div>';
  hits.forEach(function(h, i) {{
    html += '<div class="search-hit" data-hit="' + i + '">';
    html += '<div class="hit-date">' + escHtml(h.display) + ' &middot; ' + escHtml(h.source) + '</div>';
    html += '<div class="hit-title">' + escHtml(h.title) + '</div>';
    html += '<div class="hit-snippet">' + snippet(h.text, currentQuery) + '</div>';
    html += '</div>';
  }});
  container.innerHTML = html;
  document.getElementById("meetingsList").style.display = "none";
  container.querySelectorAll(".search-hit").forEach(function(el) {{
    el.addEventListener("click", function() {{ hits[parseInt(this.dataset.hit)].action(); }});
  }});
}}

document.getElementById("searchInput").addEventListener("keydown", function(e) {{ if (e.key==="Enter") runSearch(); }});
document.getElementById("searchInput").addEventListener("input", function() {{
  if (!this.value.trim()) {{
    document.getElementById("searchResults").innerHTML = "";
    document.getElementById("meetingsList").style.display = "";
    currentQuery = "";
  }}
}});

// ── Meetings list ────────────────────────────────────────────────

function renderMeetings() {{
  var meetings = DATA.meetings.filter(function(m) {{
    if (!activeKeyword) return true;
    return (m.keywords||[]).indexOf(activeKeyword) >= 0;
  }});

  var html = "";
  meetings.forEach(function(m) {{
    var hasContent = m.docs.length > 0 || m.has_caption;
    html += '<div class="meeting-row' + (hasContent ? '' : ' no-content') + '">';

    // Header row: date + year + YouTube link
    html += '<div class="row-head">';
    html += '<span class="row-date">' + escHtml(m.display || m.date) + '</span>';
    html += '<span class="row-year">' + m.date.slice(0,4) + '</span>';
    if (m.youtube_url) {{
      html += '<a class="row-yt" href="' + escHtml(m.youtube_url) + '" target="_blank" rel="noopener">&#9654; Watch</a>';
    }}
    html += '</div>';

    // Summary or placeholder
    html += '<div class="row-summary">';
    if (m.summary) {{
      html += escHtml(m.summary);
    }} else if (hasContent) {{
      html += '<em>Summary not yet generated — run summarize.py</em>';
    }} else {{
      html += '<em>No materials posted yet</em>';
    }}
    html += '</div>';

    // Keyword chips
    if (m.keywords && m.keywords.length) {{
      html += '<div class="row-keywords">';
      m.keywords.forEach(function(k) {{
        var isActive = k === activeKeyword;
        html += '<span class="kw-chip' + (isActive ? ' active' : '') + '" data-kw="' + escHtml(k) + '">' + escHtml(k) + '</span>';
      }});
      html += '</div>';
    }}

    // Document chips + transcript chip
    if (hasContent) {{
      html += '<div class="row-docs">';
      var typeOrder = ["minutes","agenda","testimony","variance","report","other"];
      typeOrder.forEach(function(t) {{
        m.docs.filter(function(d){{return d.doc_type===t;}}).forEach(function(d) {{
          html += '<span class="doc-chip ' + d.doc_type + '" data-meeting="' + escHtml(m.date) + '" data-docid="' + d.id + '">';
          html += escHtml(d.title) + '</span>';
        }});
      }});
      if (m.has_caption) {{
        html += '<span class="cap-chip" data-meeting="' + escHtml(m.date) + '">&#9654; Transcript</span>';
      }}
      html += '</div>';
    }}

    html += '</div>';
  }});

  if (!html) html = '<div style="color:#888;font-family:sans-serif;padding:20px 0">No meetings match the selected filter.</div>';
  document.getElementById("meetingsList").innerHTML = html;

  // Keyword chip clicks (filter by that keyword)
  document.querySelectorAll(".kw-chip").forEach(function(el) {{
    el.addEventListener("click", function() {{
      var kw = this.dataset.kw;
      if (activeKeyword === kw) {{
        activeKeyword = "";
      }} else {{
        activeKeyword = kw;
      }}
      // Sync bar
      document.querySelectorAll(".kw-filter").forEach(function(x) {{
        x.classList.toggle("active", x.dataset.kw === activeKeyword);
      }});
      renderMeetings();
    }});
  }});

  // Doc chip clicks
  document.querySelectorAll(".doc-chip").forEach(function(el) {{
    el.addEventListener("click", function() {{
      var docId = parseInt(this.dataset.docid);
      var dateStr = this.dataset.meeting;
      var meeting = DATA.meetings.find(function(m){{ return m.date===dateStr; }});
      if (!meeting) return;
      var doc = meeting.docs.find(function(d){{ return d.id===docId; }});
      if (!doc) return;
      openDoc(doc, DATA.doc_texts[docId] || "");
    }});
  }});

  // Transcript chip clicks
  document.querySelectorAll(".cap-chip").forEach(function(el) {{
    el.addEventListener("click", function() {{
      var dateStr = this.dataset.meeting;
      var meeting = DATA.meetings.find(function(m){{ return m.date===dateStr; }});
      if (meeting) openCaption(meeting);
    }});
  }});
}}

// ── Detail panel ────────────────────────────────────────────────

function openDoc(doc, text) {{
  document.getElementById("panelTitle").textContent = doc.title;
  document.getElementById("panelMeta").innerHTML = '<a href="' + escHtml(doc.url) + '" target="_blank" rel="noopener">Open PDF &#8599;</a> &nbsp;&middot;&nbsp; ' + doc.doc_type;
  document.getElementById("panelSearch").value = currentQuery || "";
  renderPanelBody(text || "(No text extracted from this PDF)", currentQuery || "");
  openPanel();
}}

function openCaption(meeting) {{
  document.getElementById("panelTitle").textContent = (meeting.display || meeting.date) + " — Transcript";
  var meta = meeting.youtube_url
    ? '<a href="' + escHtml(meeting.youtube_url) + '" target="_blank" rel="noopener">Watch on YouTube &#8599;</a> &nbsp;&middot;&nbsp; auto-captions'
    : 'auto-captions';
  document.getElementById("panelMeta").innerHTML = meta;
  document.getElementById("panelSearch").value = currentQuery || "";
  renderPanelBody(meeting.caption_text || "", currentQuery || "");
  openPanel();
}}

function renderPanelBody(text, query) {{
  var body = document.getElementById("panelBody");
  if (query) {{
    body.innerHTML = highlight(text, query);
    var first = body.querySelector("mark");
    if (first) first.scrollIntoView({{block:"center"}});
  }} else {{
    body.textContent = text;
    body.scrollTop = 0;
  }}
}}

function highlightInPanel(query) {{
  renderPanelBody(document.getElementById("panelBody").textContent, query);
}}

function openPanel() {{
  document.getElementById("detailPanel").classList.add("open");
  document.getElementById("overlay").classList.add("open");
}}

function closePanel() {{
  document.getElementById("detailPanel").classList.remove("open");
  document.getElementById("overlay").classList.remove("open");
}}

document.addEventListener("keydown", function(e) {{ if (e.key==="Escape") closePanel(); }});

// ── Init ──────────────────────────────────────────────────────────
buildKeywordBar();
renderMeetings();
</script>
</body>
</html>"""


if __name__ == "__main__":
    main()
