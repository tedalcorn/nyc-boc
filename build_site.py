"""
Build the NYC BOC viewer site.
Reads boc.db and writes site/index.html with embedded JSON.

Run: python build_site.py
"""

import json
import re
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "boc.db"
SITE_DIR = Path(__file__).parent / "site"
OUT_HTML = SITE_DIR / "index.html"

def main():
    SITE_DIR.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    meetings = []
    for m in conn.execute("SELECT * FROM meetings ORDER BY date DESC"):
        docs = []
        for d in conn.execute(
            "SELECT id, title, url, filename, doc_type, downloaded, extracted FROM documents WHERE meeting_id=? ORDER BY doc_type, title",
            (m["id"],)
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

        caption_preview = ""
        caption_text = ""
        if cap and cap["text"]:
            caption_text = cap["text"]
            # Skip the boilerplate disclaimer at the top
            lines = caption_text.splitlines()
            for i, line in enumerate(lines):
                if ">>" in line or (i > 8 and line.strip()):
                    caption_preview = " ".join(lines[i:i+4])
                    break

        meetings.append({
            "date": m["date"],
            "display": m["display"] or m["date"],
            "page_url": m["page_url"],
            "youtube_id": m["youtube_id"],
            "youtube_url": m["youtube_url"],
            "docs": docs,
            "has_caption": bool(cap and cap["text"]),
            "caption_source": cap["source"] if cap else None,
            "caption_preview": caption_preview[:200] if caption_preview else "",
            "caption_text": caption_text,
        })

    # Build doc text lookup for search: {doc_id: text}
    doc_texts = {}
    for row in conn.execute("SELECT id, text FROM documents WHERE extracted=1 AND text IS NOT NULL"):
        doc_texts[row["id"]] = row["text"]

    conn.close()

    data = {
        "meetings": meetings,
        "doc_texts": doc_texts,
    }

    data_js = "const DATA = " + json.dumps(data, ensure_ascii=False, separators=(",", ":")) + ";"

    html = build_html(data_js)
    OUT_HTML.write_text(html, encoding="utf-8")
    print(f"Wrote {OUT_HTML}")
    print(f"  {len(meetings)} meetings, {sum(len(m['docs']) for m in meetings)} documents")
    print(f"  {sum(1 for m in meetings if m['has_caption'])} meetings with captions")


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
  padding: 18px 28px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
}}
header h1 {{ font-size: 20px; font-weight: normal; letter-spacing: 0.02em; }}
header .subtitle {{ font-size: 12px; opacity: 0.7; margin-top: 3px; }}

.search-bar {{
  display: flex;
  gap: 8px;
  align-items: center;
  flex-wrap: wrap;
}}
.search-bar input {{
  padding: 7px 12px;
  font-size: 14px;
  border: none;
  border-radius: 4px;
  width: 280px;
  font-family: inherit;
}}
.search-bar select {{
  padding: 7px 10px;
  font-size: 13px;
  border: none;
  border-radius: 4px;
  font-family: inherit;
  background: #fff;
}}
.search-bar button {{
  padding: 7px 16px;
  font-size: 13px;
  background: #e8a020;
  color: #fff;
  border: none;
  border-radius: 4px;
  cursor: pointer;
  font-family: inherit;
}}
.search-bar button:hover {{ background: #c88010; }}

.container {{ max-width: 1100px; margin: 0 auto; padding: 24px 20px; }}

/* Search results */
#searchResults {{ margin-bottom: 24px; }}
.search-hit {{
  background: #fff;
  border-left: 3px solid #326891;
  border-radius: 0 4px 4px 0;
  padding: 12px 16px;
  margin-bottom: 10px;
  cursor: pointer;
}}
.search-hit:hover {{ background: #eef4f8; }}
.search-hit .hit-date {{ font-size: 12px; color: #888; }}
.search-hit .hit-title {{ font-weight: bold; font-size: 14px; margin: 2px 0 4px; }}
.search-hit .hit-snippet {{ font-size: 13px; color: #444; font-family: sans-serif; }}
.search-hit .hit-snippet mark {{ background: #ffe066; padding: 0 1px; }}
.search-hit .hit-source {{ font-size: 11px; color: #888; margin-top: 4px; font-family: sans-serif; }}
.no-results {{ color: #888; font-style: italic; padding: 16px 0; font-family: sans-serif; }}

/* Meeting cards */
.meetings-grid {{
  display: grid;
  gap: 16px;
}}
.meeting-card {{
  background: #fff;
  border-radius: 6px;
  box-shadow: 0 1px 3px rgba(0,0,0,0.08);
  overflow: hidden;
}}
.meeting-header {{
  background: #1a3a5c;
  color: #fff;
  padding: 12px 18px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}}
.meeting-header h2 {{ font-size: 17px; font-weight: bold; }}
.meeting-header .yt-link {{
  display: inline-flex;
  align-items: center;
  gap: 5px;
  background: #c00;
  color: #fff;
  padding: 4px 10px;
  border-radius: 4px;
  font-size: 12px;
  font-family: sans-serif;
  white-space: nowrap;
}}
.meeting-header .yt-link:hover {{ background: #a00; text-decoration: none; }}
.meeting-body {{ padding: 14px 18px; }}

.docs-section {{ margin-bottom: 12px; }}
.docs-section h3 {{ font-size: 11px; text-transform: uppercase; letter-spacing: 0.08em; color: #888; margin-bottom: 6px; font-family: sans-serif; }}
.doc-list {{ display: flex; flex-wrap: wrap; gap: 6px; }}
.doc-chip {{
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 4px 10px;
  border-radius: 12px;
  font-size: 12px;
  font-family: sans-serif;
  cursor: pointer;
  border: 1px solid transparent;
  transition: background 0.15s;
}}
.doc-chip.minutes   {{ background: #ddeeff; color: #1a3a5c; border-color: #b0d0f0; }}
.doc-chip.agenda    {{ background: #e8f4e8; color: #1a4a1a; border-color: #a0d0a0; }}
.doc-chip.testimony {{ background: #fff3dd; color: #5a3a00; border-color: #e0c080; }}
.doc-chip.variance  {{ background: #f5e8f5; color: #4a1a4a; border-color: #d0a0d0; }}
.doc-chip.report    {{ background: #f0f0f0; color: #333; border-color: #ccc; }}
.doc-chip.other     {{ background: #f5f5f5; color: #555; border-color: #ddd; }}
.doc-chip:hover {{ opacity: 0.8; }}

.caption-preview {{
  margin-top: 10px;
  padding: 10px 14px;
  background: #f8f6f0;
  border-radius: 4px;
  font-size: 13px;
  color: #555;
  font-family: sans-serif;
  cursor: pointer;
  border-left: 3px solid #e8a020;
}}
.caption-preview:hover {{ background: #f0ece0; }}
.caption-preview .cap-label {{ font-size: 11px; text-transform: uppercase; letter-spacing: 0.06em; color: #e8a020; margin-bottom: 3px; font-weight: bold; }}

.no-docs {{ font-size: 13px; color: #aaa; font-family: sans-serif; font-style: italic; }}

/* Detail panel */
#detailPanel {{
  position: fixed;
  top: 0; right: 0;
  width: 52vw;
  max-width: 780px;
  min-width: 340px;
  height: 100vh;
  background: #fff;
  box-shadow: -4px 0 20px rgba(0,0,0,0.15);
  display: flex;
  flex-direction: column;
  z-index: 100;
  transform: translateX(100%);
  transition: transform 0.25s ease;
}}
#detailPanel.open {{ transform: translateX(0); }}
.panel-header {{
  background: #1a3a5c;
  color: #fff;
  padding: 14px 18px;
  display: flex;
  align-items: flex-start;
  gap: 10px;
}}
.panel-header h3 {{ flex: 1; font-size: 16px; font-weight: bold; line-height: 1.3; }}
.panel-close {{
  background: none;
  border: none;
  color: #fff;
  font-size: 22px;
  cursor: pointer;
  line-height: 1;
  opacity: 0.7;
  padding: 0 4px;
  flex-shrink: 0;
}}
.panel-close:hover {{ opacity: 1; }}
.panel-meta {{ padding: 10px 18px; background: #f0ece0; font-size: 12px; color: #666; font-family: sans-serif; display: flex; gap: 12px; flex-wrap: wrap; }}
.panel-meta a {{ color: #326891; }}
.panel-search {{
  padding: 10px 18px;
  background: #f8f8f8;
  border-bottom: 1px solid #eee;
}}
.panel-search input {{
  width: 100%;
  padding: 6px 10px;
  font-size: 13px;
  border: 1px solid #ddd;
  border-radius: 4px;
  font-family: sans-serif;
}}
.panel-body {{
  flex: 1;
  overflow-y: auto;
  padding: 16px 18px;
  font-family: sans-serif;
  font-size: 13px;
  line-height: 1.6;
  color: #333;
  white-space: pre-wrap;
  word-wrap: break-word;
}}
.panel-body mark {{ background: #ffe066; }}

#overlay {{
  display: none;
  position: fixed;
  inset: 0;
  background: rgba(0,0,0,0.3);
  z-index: 99;
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
    <div class="subtitle">Meeting Archive — Minutes, Testimony, and Video Transcripts</div>
  </div>
  <div class="search-bar">
    <input type="text" id="searchInput" placeholder="Search minutes, testimony, transcripts..." autocomplete="off">
    <select id="searchScope">
      <option value="all">All content</option>
      <option value="minutes">Minutes only</option>
      <option value="testimony">Testimony only</option>
      <option value="captions">Transcripts only</option>
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
  <div id="searchResults"></div>
  <div id="meetingsList"></div>
</div>

<script>
{data_js}

var currentQuery = "";

function escHtml(s) {{
  return (s||"").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
}}

function highlight(text, query) {{
  if (!query) return escHtml(text);
  var escaped = escHtml(text);
  var re = new RegExp("(" + query.replace(/[.*+?^${{}}()|[\\]\\\\]/g,"\\\\$&") + ")", "gi");
  return escaped.replace(re, "<mark>$1</mark>");
}}

function snippet(text, query, radius) {{
  radius = radius || 150;
  if (!query || !text) return escHtml((text||"").slice(0,200));
  var idx = text.toLowerCase().indexOf(query.toLowerCase());
  if (idx < 0) return escHtml(text.slice(0,200));
  var start = Math.max(0, idx - radius);
  var end = Math.min(text.length, idx + query.length + radius);
  var chunk = (start > 0 ? "..." : "") + text.slice(start, end) + (end < text.length ? "..." : "");
  return highlight(chunk, query);
}}

function runSearch() {{
  currentQuery = document.getElementById("searchInput").value.trim();
  var scope = document.getElementById("searchScope").value;
  var container = document.getElementById("searchResults");
  if (!currentQuery) {{
    container.innerHTML = "";
    renderMeetings(DATA.meetings);
    return;
  }}
  var q = currentQuery.toLowerCase();
  var hits = [];

  DATA.meetings.forEach(function(m) {{
    // Search captions
    if (scope === "all" || scope === "captions") {{
      if (m.has_caption && m.caption_text && m.caption_text.toLowerCase().indexOf(q) >= 0) {{
        hits.push({{
          date: m.date,
          display: m.display,
          title: "Video Transcript",
          source: "transcript",
          text: m.caption_text,
          action: function(m_) {{ return function() {{ openCaption(m_); }}; }}(m),
        }});
      }}
    }}
    // Search documents
    m.docs.forEach(function(d) {{
      if (scope !== "all" && scope !== d.doc_type) return;
      var text = DATA.doc_texts[d.id] || "";
      if (text && text.toLowerCase().indexOf(q) >= 0) {{
        hits.push({{
          date: m.date,
          display: m.display,
          title: d.title,
          source: d.doc_type,
          text: text,
          url: d.url,
          action: function(d_, text_) {{ return function() {{ openDoc(d_, text_); }}; }}(d, text),
        }});
      }}
    }});
  }});

  // Sort by date descending
  hits.sort(function(a,b) {{ return b.date < a.date ? -1 : 1; }});

  if (hits.length === 0) {{
    container.innerHTML = "<div class=\\"no-results\\">No results for &ldquo;" + escHtml(currentQuery) + "&rdquo;</div>";
    document.getElementById("meetingsList").style.display = "";
    return;
  }}

  var html = "<div style=\\"font-size:13px;color:#666;font-family:sans-serif;margin-bottom:10px\\">" + hits.length + " result" + (hits.length === 1 ? "" : "s") + " for &ldquo;" + escHtml(currentQuery) + "&rdquo;</div>";
  hits.forEach(function(h, i) {{
    html += "<div class=\\"search-hit\\" data-hit=\\"" + i + "\\">";
    html += "<div class=\\"hit-date\\">" + escHtml(h.display) + " &middot; " + escHtml(h.source) + "</div>";
    html += "<div class=\\"hit-title\\">" + escHtml(h.title) + "</div>";
    html += "<div class=\\"hit-snippet\\">" + snippet(h.text, currentQuery) + "</div>";
    html += "</div>";
  }});
  container.innerHTML = html;
  document.getElementById("meetingsList").style.display = "none";

  // Attach click handlers
  container.querySelectorAll(".search-hit").forEach(function(el) {{
    el.addEventListener("click", function() {{
      var i = parseInt(this.dataset.hit);
      hits[i].action();
    }});
  }});
}}

document.getElementById("searchInput").addEventListener("keydown", function(e) {{
  if (e.key === "Enter") runSearch();
}});
document.getElementById("searchInput").addEventListener("input", function() {{
  if (!this.value.trim()) {{
    document.getElementById("searchResults").innerHTML = "";
    document.getElementById("meetingsList").style.display = "";
    currentQuery = "";
  }}
}});

// ── Meetings list ──────────────────────────────────────────────────

function renderMeetings(meetings) {{
  var html = "<div class=\\"meetings-grid\\">";
  meetings.forEach(function(m) {{
    html += "<div class=\\"meeting-card\\">";
    html += "<div class=\\"meeting-header\\">";
    html += "<h2>" + escHtml(m.display || m.date) + " &nbsp;<span style=\\"font-size:13px;font-weight:normal;opacity:0.7\\">" + m.date.slice(0,4) + "</span></h2>";
    if (m.youtube_url) {{
      html += "<a class=\\"yt-link\\" href=\\"" + escHtml(m.youtube_url) + "\\" target=\\"_blank\\" rel=\\"noopener\\">&#9654; Watch</a>";
    }}
    html += "</div>";
    html += "<div class=\\"meeting-body\\">";

    if (m.docs.length === 0 && !m.has_caption) {{
      html += "<div class=\\"no-docs\\">No materials posted yet</div>";
    }} else {{
      // Group docs by type
      var byType = {{}};
      m.docs.forEach(function(d) {{
        if (!byType[d.doc_type]) byType[d.doc_type] = [];
        byType[d.doc_type].push(d);
      }});
      var typeOrder = ["minutes","agenda","testimony","variance","report","other"];
      var typeLabels = {{minutes:"Minutes",agenda:"Agenda",testimony:"Testimony & Comments",variance:"Variance Records",report:"Reports",other:"Other Documents"}};
      typeOrder.forEach(function(t) {{
        if (!byType[t]) return;
        html += "<div class=\\"docs-section\\">";
        html += "<h3>" + typeLabels[t] + "</h3>";
        html += "<div class=\\"doc-list\\">";
        byType[t].forEach(function(d) {{
          var text = DATA.doc_texts[d.id] || "";
          html += "<span class=\\"doc-chip " + d.doc_type + "\\" data-meeting=\\"" + escHtml(m.date) + "\\" data-docid=\\"" + d.id + "\\">";
          html += escHtml(d.title);
          html += "</span>";
        }});
        html += "</div></div>";
      }});

      if (m.has_caption) {{
        html += "<div class=\\"caption-preview\\" data-meeting=\\"" + escHtml(m.date) + "\\">";
        html += "<div class=\\"cap-label\\">&#9654; Video Transcript (auto-captions)</div>";
        html += escHtml(m.caption_preview || "Click to read transcript");
        html += "...</div>";
      }}
    }}

    html += "</div></div>";
  }});
  html += "</div>";
  document.getElementById("meetingsList").innerHTML = html;

  // Click handlers for doc chips
  document.querySelectorAll(".doc-chip").forEach(function(el) {{
    el.addEventListener("click", function() {{
      var docId = parseInt(this.dataset.docid);
      var dateStr = this.dataset.meeting;
      var meeting = DATA.meetings.find(function(m) {{ return m.date === dateStr; }});
      if (!meeting) return;
      var doc = meeting.docs.find(function(d) {{ return d.id === docId; }});
      if (!doc) return;
      var text = DATA.doc_texts[docId] || "";
      openDoc(doc, text);
    }});
  }});

  // Click handlers for caption preview
  document.querySelectorAll(".caption-preview").forEach(function(el) {{
    el.addEventListener("click", function() {{
      var dateStr = this.dataset.meeting;
      var meeting = DATA.meetings.find(function(m) {{ return m.date === dateStr; }});
      if (meeting) openCaption(meeting);
    }});
  }});
}}

// ── Detail panel ───────────────────────────────────────────────────

function openDoc(doc, text) {{
  document.getElementById("panelTitle").textContent = doc.title;
  var meta = "<a href=\\"" + escHtml(doc.url) + "\\" target=\\"_blank\\" rel=\\"noopener\\">Open PDF &#8599;</a>";
  meta += " &nbsp;&middot;&nbsp; " + doc.doc_type;
  document.getElementById("panelMeta").innerHTML = meta;
  document.getElementById("panelSearch").value = currentQuery || "";
  renderPanelBody(text || "(No text extracted from this PDF)", currentQuery || "");
  openPanel();
}}

function openCaption(meeting) {{
  document.getElementById("panelTitle").textContent = (meeting.display || meeting.date) + " — Video Transcript";
  var meta = "";
  if (meeting.youtube_url) {{
    meta = "<a href=\\"" + escHtml(meeting.youtube_url) + "\\" target=\\"_blank\\" rel=\\"noopener\\">Watch on YouTube &#8599;</a> &nbsp;&middot;&nbsp; auto-captions";
  }} else {{
    meta = "auto-captions";
  }}
  document.getElementById("panelMeta").innerHTML = meta;
  document.getElementById("panelSearch").value = currentQuery || "";
  renderPanelBody(meeting.caption_text || "", currentQuery || "");
  openPanel();
}}

function renderPanelBody(text, query) {{
  var body = document.getElementById("panelBody");
  if (query) {{
    body.innerHTML = highlight(text, query);
    // Scroll to first hit
    var firstMark = body.querySelector("mark");
    if (firstMark) firstMark.scrollIntoView({{block:"center"}});
  }} else {{
    body.textContent = text;
    body.scrollTop = 0;
  }}
}}

function highlightInPanel(query) {{
  var text = document.getElementById("panelBody").textContent;
  renderPanelBody(text, query);
}}

function openPanel() {{
  document.getElementById("detailPanel").classList.add("open");
  document.getElementById("overlay").classList.add("open");
}}

function closePanel() {{
  document.getElementById("detailPanel").classList.remove("open");
  document.getElementById("overlay").classList.remove("open");
}}

document.addEventListener("keydown", function(e) {{
  if (e.key === "Escape") closePanel();
}});

// ── Init ──────────────────────────────────────────────────────────
renderMeetings(DATA.meetings);
</script>
</body>
</html>"""


if __name__ == "__main__":
    main()
