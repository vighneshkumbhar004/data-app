#!/usr/bin/env python3
import os
import csv
import json
from datetime import datetime
from typing import List, Dict, Any

from flask import Flask, request, redirect, url_for, render_template_string, send_from_directory, flash

# Import the existing processing logic
import process_docs as pd

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(ROOT, "output")
UPLOAD_DIR = os.path.join(ROOT, "uploads")
CSV_PATH = os.path.join(OUT_DIR, "summary.csv")

os.makedirs(OUT_DIR, exist_ok=True)
os.makedirs(UPLOAD_DIR, exist_ok=True)

app = Flask(__name__)
app.secret_key = "dev-secret-change-me"  # for flash messages only

ALLOWED_EXTS = pd.SUPPORTED_EXTS


# ------- Helpers -------
def allowed_file(filename: str) -> bool:
    ext = os.path.splitext(filename)[1].lower()
    return ext in ALLOWED_EXTS


def append_outputs(ds: pd.DocSummary):
    # CSV
    csv_new = not os.path.exists(CSV_PATH)
    with open(CSV_PATH, "a", encoding="utf-8", newline="") as csvf:
        writer = csv.writer(csvf)
        if csv_new:
            writer.writerow(pd.DocSummary.csv_header())
        writer.writerow(ds.to_csv_row())

    # Per-tag JSONL routes
    def write_route(tag: str, record: Dict[str, Any]):
        fname = os.path.join(OUT_DIR, f"route_{pd.sanitize_filename(tag)}.jsonl")
        with open(fname, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    rec = {
        "source_path": ds.source_path,
        "file_name": ds.file_name,
        "file_sha256": ds.file_sha256,
        "language": ds.language,
        "title": ds.title,
        "summary_sentences": ds.summary_sentences,
        "action_items": ds.action_items,
        "tags": ds.tags,
        "detected_dates": ds.detected_dates,
        "detected_amounts": ds.detected_amounts,
        "first_seen_at": ds.first_seen_at,
    }
    for tag in ds.tags:
        write_route(tag, rec)

    # Per-file JSON
    per_json = os.path.join(OUT_DIR, pd.sanitize_filename(ds.file_name) + ".json")
    with open(per_json, "w", encoding="utf-8") as jf:
        json.dump(rec, jf, ensure_ascii=False, indent=2)


def read_csv_rows() -> List[Dict[str, Any]]:
    if not os.path.exists(CSV_PATH):
        return []
    rows: List[Dict[str, Any]] = []
    with open(CSV_PATH, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for r in reader:
            # Compute convenience fields
            per_json_name = pd.sanitize_filename(r["file_name"]) + ".json"
            json_path = os.path.join(OUT_DIR, per_json_name)
            r = dict(r)
            r["per_json"] = per_json_name
            r["has_json"] = os.path.exists(json_path)
            rows.append(r)
    rows.sort(key=lambda r: r.get("first_seen_at", ""), reverse=True)
    return rows


def get_detail_by_sha(file_sha256: str) -> Dict[str, Any]:
    rows = read_csv_rows()
    for r in rows:
        if r.get("file_sha256") == file_sha256:
            # Load the per-file JSON if present; else reconstruct from CSV row
            json_path = os.path.join(OUT_DIR, pd.sanitize_filename(r["file_name"]) + ".json")
            if os.path.exists(json_path):
                with open(json_path, "r", encoding="utf-8") as jf:
                    return json.load(jf)
            # Fallback: minimal reconstruction
            return {
                "source_path": r.get("source_path"),
                "file_name": r.get("file_name"),
                "file_sha256": r.get("file_sha256"),
                "language": r.get("language"),
                "title": r.get("title"),
                "summary_sentences": (r.get("summary") or "").split(" • ") if r.get("summary") else [],
                "action_items": (r.get("action_items") or "").split(" | ") if r.get("action_items") else [],
                "tags": (r.get("tags") or "").split("; ") if r.get("tags") else ["General"],
                "detected_dates": (r.get("detected_dates") or "").split("; ") if r.get("detected_dates") else [],
                "detected_amounts": (r.get("detected_amounts") or "").split("; ") if r.get("detected_amounts") else [],
                "first_seen_at": r.get("first_seen_at"),
            }
    raise FileNotFoundError("Summary not found for sha256: " + file_sha256)


# ------- Routes -------
INDEX_HTML = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <title>KMRL Document Summarizer</title>
  <style>
    body { font-family: system-ui, Segoe UI, Arial, sans-serif; margin: 24px; }
    header { display: flex; align-items: center; justify-content: space-between; }
    .flash { background: #fff3cd; padding: 8px 12px; border: 1px solid #ffeeba; margin: 12px 0; }
    form.box { margin: 16px 0; padding: 12px; border: 1px solid #ddd; background: #fafafa; }
    input[type=file] { margin-right: 8px; }
    table { border-collapse: collapse; width: 100%; }
    th, td { border: 1px solid #ddd; padding: 8px; font-size: 14px; }
    th { background: #f0f0f0; text-align: left; }
    .tag { background: #eef; border: 1px solid #ccd; padding: 2px 6px; margin-right: 4px; border-radius: 4px; display: inline-block; }
    .filters { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
    .filters label { font-size: 12px; color: #333; }
  </style>
</head>
<body>
  <header>
    <h1>KMRL Document Summarizer</h1>
    <div><a href="{{ url_for('index') }}">Home</a></div>
  </header>

  {% with messages = get_flashed_messages() %}
    {% if messages %}
      {% for m in messages %}<div class="flash">{{ m }}</div>{% endfor %}
    {% endif %}
  {% endwith %}

  <form class="box" action="{{ url_for('upload') }}" method="post" enctype="multipart/form-data">
    <label>Upload documents (PDF/DOCX/TXT):</label>
    <input type="file" name="files" multiple />
    <button type="submit">Upload & Summarize</button>
  </form>

  <form class="box" method="get" action="{{ url_for('index') }}">
    <div class="filters">
      <label>Search: <input type="text" name="q" value="{{ q or '' }}" placeholder="name, title, summary" /></label>
      <label>Tag:
        <select name="tag">
          <option value="">All</option>
          {% for t in available_tags %}
            <option value="{{ t }}" {% if tag==t %}selected{% endif %}>{{ t }}</option>
          {% endfor %}
        </select>
      </label>
      <label>Language:
        <select name="lang">
          <option value="">All</option>
          {% for l in available_langs %}
            <option value="{{ l }}" {% if lang==l %}selected{% endif %}>{{ l }}</option>
          {% endfor %}
        </select>
      </label>
      <button type="submit">Apply</button>
      <a href="{{ url_for('index') }}">Reset</a>
    </div>
  </form>

  <h2>Summaries</h2>
  {% if rows %}
  <table>
    <thead>
      <tr>
        <th>File</th>
        <th>Language</th>
        <th>Tags</th>
        <th>Detected Dates</th>
        <th>Amounts</th>
        <th>First Seen</th>
        <th>Downloads</th>
      </tr>
    </thead>
    <tbody>
      {% for r in rows %}
      <tr>
        <td><a href="{{ url_for('detail', file_sha256=r['file_sha256']) }}">{{ r['file_name'] }}</a></td>
        <td>{{ r['language'] }}</td>
        <td>
          {% for t in (r['tags'].split('; ') if r['tags'] else ['General']) %}
            <span class="tag">{{ t }}</span>
          {% endfor %}
        </td>
        <td>{{ r['detected_dates'] }}</td>
        <td>{{ r['detected_amounts'] }}</td>
        <td>{{ r['first_seen_at'] }}</td>
        <td>
          {% if r['has_json'] %}
            <a href="{{ url_for('download', filename=r['per_json']) }}">JSON</a>
          {% else %}
            —
          {% endif %}
        </td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
  {% else %}
    <p>No summaries yet. Upload a document to get started.</p>
  {% endif %}
</body>
</html>
"""


DETAIL_HTML = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <title>{{ d['file_name'] }} – Summary</title>
  <style>
    body { font-family: system-ui, Segoe UI, Arial, sans-serif; margin: 24px; }
    .tag { background: #eef; border: 1px solid #ccd; padding: 2px 6px; margin-right: 4px; border-radius: 4px; display: inline-block; }
    ul { margin: 0 0 12px 20px; }
    pre { background: #f8f8f8; padding: 12px; overflow-x: auto; }
  </style>
</head>
<body>
  <p><a href="{{ url_for('index') }}">← Back</a></p>
  <h1>{{ d['title'] or d['file_name'] }}</h1>
  <p>
    {% for t in d['tags'] %}<span class="tag">{{ t }}</span>{% endfor %}
  </p>
  <h3>Summary</h3>
  <ul>
    {% for s in d['summary_sentences'] %}<li>{{ s }}</li>{% endfor %}
  </ul>
  <h3>Action Items</h3>
  {% if d['action_items'] %}
  <ul>
    {% for a in d['action_items'] %}<li>{{ a }}</li>{% endfor %}
  </ul>
  {% else %}
  <p>No explicit actions detected.</p>
  {% endif %}
<h3>Metadata</h3>
  <p>
    <a href="{{ url_for('download', filename=(d['file_name'] | replace(' ', '_')) + '.json') }}">Download JSON</a>
  </p>
  <pre>{{ d | tojson(indent=2) }}</pre>
</body>
</html>
"""


@app.route("/")
def index():
    rows = read_csv_rows()

    # Build filter options
    tag_set = set()
    lang_set = set()
    for r in rows:
        if r.get("tags"):
            for t in r["tags"].split("; "):
                if t:
                    tag_set.add(t)
        if r.get("language"):
            lang_set.add(r["language"])
    available_tags = sorted(tag_set)
    available_langs = sorted(lang_set)

    # Read filters
    q = request.args.get("q", default="").strip()
    tag = request.args.get("tag", default="").strip()
    lang = request.args.get("lang", default="").strip()

    ql = q.lower()
    def match(r: Dict[str, Any]) -> bool:
        if tag and (tag not in (r.get("tags") or "")):
            return False
        if lang and (r.get("language") != lang):
            return False
        if q:
            hay = " ".join([
                r.get("file_name", ""),
                r.get("title", ""),
                r.get("summary", ""),
                r.get("action_items", ""),
                r.get("detected_dates", ""),
                r.get("detected_amounts", ""),
            ]).lower()
            if ql not in hay:
                return False
        return True

    filtered = [r for r in rows if match(r)]

    return render_template_string(
        INDEX_HTML,
        rows=filtered,
        q=q,
        tag=tag,
        lang=lang,
        available_tags=available_tags,
        available_langs=available_langs,
    )


@app.route("/upload", methods=["POST"])
def upload():
    if "files" not in request.files:
        flash("No files provided")
        return redirect(url_for("index"))

    files = request.files.getlist("files")
    processed = 0
    rejected = 0

    for file in files:
        if file.filename == "":
            continue
        if not allowed_file(file.filename):
            rejected += 1
            continue
        safe_name = pd.sanitize_filename(file.filename)
        save_path = os.path.join(UPLOAD_DIR, safe_name)
        file.save(save_path)

        # Process the uploaded file
        ds = pd.process(save_path, max_sentences=5)
        if ds:
            append_outputs(ds)
            processed += 1
        else:
            rejected += 1

    flash(f"Uploaded/processed: {processed}; rejected: {rejected}")
    return redirect(url_for("index"))


@app.route("/detail/<file_sha256>")
def detail(file_sha256: str):
    d = get_detail_by_sha(file_sha256)
    return render_template_string(DETAIL_HTML, d=d)


@app.route("/download/<path:filename>")
def download(filename: str):
    # Allow downloading per-file JSONs or route files
    return send_from_directory(OUT_DIR, filename, as_attachment=True)


if __name__ == "__main__":
    debug = os.environ.get("FLASK_DEBUG", "1") == "1"
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="127.0.0.1", port=port, debug=debug)
