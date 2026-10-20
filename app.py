"""Dashboard that scrapes a profile on demand and shows the saved data, likes and comments."""

from __future__ import annotations

import json
import re
import threading
import webbrowser
from collections import Counter
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request

import scraper

DATA_DIR = Path(__file__).parent / "data"
URL = "http://127.0.0.1:5000"
USERNAME_PATTERN = re.compile(r"[A-Za-z0-9._]{1,30}")
MAX_POSTS = 200
MAX_DETAILS = 30
STOPWORDS = {
    "photo", "image", "may", "with", "that", "this", "from", "and", "the", "for", "text",
    "are", "can", "has", "have", "was", "who", "his", "her", "their", "there", "into",
    "foto", "imagen", "puede", "ser", "una", "con", "los", "las", "del", "por", "que",
    "para", "texto",
}

app = Flask(__name__)
job = {
    "state": "idle", "username": None, "stage": None, "progress": 0, "total": 0,
    "message": None, "error": None,
}
job_lock = threading.Lock()


def load_json(filename: str, default):
    """Return the parsed content of a file in data/, or `default` if it does not exist yet."""
    path = DATA_DIR / filename
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def average(values: list[int]) -> int | None:
    """Return the rounded mean of `values`, or None when there is nothing to average."""
    return round(sum(values) / len(values)) if values else None


def compute_engagement(posts: list[dict]) -> dict:
    """Summarize likes and comments over the posts that were opened in detail."""
    detailed = [post for post in posts if "caption" in post]
    liked = [post for post in detailed if post.get("likes") is not None]
    top = max(liked, key=lambda post: post["likes"], default=None)
    return {
        "detailed": len(detailed),
        "avg_likes": average([post["likes"] for post in liked]),
        "avg_comments": average([post["comments_count"] for post in detailed if post.get("comments_count") is not None]),
        "comments_read": sum(len(post["comments"]) for post in detailed),
        "top_url": top["url"] if top else None,
        "top_likes": top["likes"] if top else None,
    }


def compute_stats(posts: list[dict]) -> dict:
    """Summarize posts: content mix, description coverage, frequent words and engagement."""
    lengths = [len(post["description"]) for post in posts if post["description"]]
    words = Counter(
        word
        for post in posts
        for word in re.findall(r"[^\W\d_]{4,}", (post.get("caption") or post["description"]).lower())
        if word not in STOPWORDS
    )
    return {
        "engagement": compute_engagement(posts),
        "total": len(posts),
        "types": dict(Counter(post["type"] for post in posts)),
        "described": len(lengths),
        "avg_description_length": round(sum(lengths) / len(lengths)) if lengths else 0,
        "top_words": [{"word": word, "count": count} for word, count in words.most_common(10)],
    }


def run_job(username: str, target: int, details: int, headless: bool) -> None:
    """Run the scraper in a background thread and publish its outcome in `job`."""
    try:
        scraper.run(
            username,
            target,
            headless,
            details,
            on_progress=lambda stage, done, total: job.update(stage=stage, progress=done, total=total),
            on_status=lambda message: job.update(message=message),
        )
        job.update(state="done")
    except Exception as error:  # any failure must reach the UI instead of dying in the thread
        job.update(state="error", error=str(error))


@app.get("/")
def index():
    """Serve the dashboard page."""
    return render_template("index.html")


@app.get("/api/data")
def api_data():
    """Return the saved profile, posts and computed statistics as JSON."""
    posts = load_json("posts.json", [])
    return jsonify(profile=load_json("profile.json", None), posts=posts, stats=compute_stats(posts))


@app.post("/api/scrape")
def api_scrape():
    """Validate the request and start a scraping job in the background."""
    body = request.get_json(silent=True) or {}
    username = str(body.get("username", "")).strip().lstrip("@")
    if not USERNAME_PATTERN.fullmatch(username):
        return jsonify(error="Enter a valid username: letters, numbers, dots and underscores."), 400
    try:
        target = min(max(int(body.get("posts", 50)), 1), MAX_POSTS)
        details = min(max(int(body.get("details", scraper.DEFAULT_DETAILS)), 0), MAX_DETAILS)
    except (TypeError, ValueError):
        return jsonify(error="Posts and detailed posts must be numbers."), 400

    with job_lock:
        if job["state"] == "running":
            return jsonify(error="A scraping job is already running."), 409
        job.update(state="running", username=username, stage=None, progress=0, total=target, message=None, error=None)
    threading.Thread(target=run_job, args=(username, target, details, bool(body.get("headless"))), daemon=True).start()
    return jsonify(job), 202


@app.get("/api/status")
def api_status():
    """Return the state of the current scraping job."""
    return jsonify(job)


def main() -> None:
    """Load the session cookie, open the browser and start the server."""
    load_dotenv()
    threading.Timer(1.0, webbrowser.open, args=(URL,)).start()
    app.run(port=5000)


if __name__ == "__main__":
    main()
