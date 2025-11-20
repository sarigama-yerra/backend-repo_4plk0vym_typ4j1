import os
import re
import time
from urllib.parse import urljoin, urlparse
from typing import List, Optional

import requests
from bs4 import BeautifulSoup
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from database import db, create_document, get_documents

app = FastAPI(title="AI SEO Audit API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class StartCrawlRequest(BaseModel):
    url: str


class TaskIdResponse(BaseModel):
    task_id: str


def normalize_url(base: str, link: str) -> Optional[str]:
    try:
        u = urljoin(base, link)
        parsed = urlparse(u)
        if parsed.scheme in {"http", "https"}:
            # strip fragments
            return parsed._replace(fragment="").geturl()
    except Exception:
        return None
    return None


def same_origin(a: str, b: str) -> bool:
    pa, pb = urlparse(a), urlparse(b)
    return pa.netloc == pb.netloc


@app.get("/")
def root():
    return {"status": "ok", "name": "AI SEO Audit API"}


@app.post("/api/crawl/start", response_model=TaskIdResponse)
def start_crawl(payload: StartCrawlRequest):
    if not payload.url:
        raise HTTPException(400, "URL is required")
    # create crawl document
    from schemas import CrawlTask

    crawl = CrawlTask(seed_url=payload.url)
    task_id = create_document("crawltask", crawl)
    return {"task_id": task_id}


@app.get("/api/crawl/status")
def crawl_status(task_id: str):
    # fetch crawl task
    docs = get_documents("crawltask", {"_id": {"$oid": task_id}})
    # fallback: try string compare if viewer doesn't construct $oid
    if not docs:
        from bson import ObjectId
        try:
            docs = get_documents("crawltask", {"_id": ObjectId(task_id)})
        except Exception:
            pass
    if not docs:
        raise HTTPException(404, "Task not found")
    doc = docs[0]
    # Do a lightweight crawler step each call to simulate progress
    seed = doc.get("seed_url")
    visited = set(doc.get("urls", [])[:100])
    to_visit = []
    if not visited:
        to_visit = [seed]
    # fetch at most a few pages to simulate progressive crawling
    new_urls: List[str] = list(visited)
    steps = 0
    while to_visit and steps < 2 and len(new_urls) < 100:
        current = to_visit.pop(0)
        try:
            r = requests.get(current, timeout=6, headers={"User-Agent": "SEO-Audit-Bot/1.0"})
            if r.status_code != 200:
                steps += 1
                continue
            soup = BeautifulSoup(r.text, "html.parser")
            for a in soup.find_all("a", href=True):
                nu = normalize_url(current, a["href"]) or ""
                if not nu:
                    continue
                if same_origin(seed, nu) and nu not in new_urls and len(new_urls) < 100:
                    new_urls.append(nu)
                    to_visit.append(nu)
        except Exception:
            pass
        steps += 1
    progress = min(100, len(new_urls) // 1)  # simple proxy
    # update document
    try:
        db["crawltask"].update_one(
            {"_id": doc["_id"]},
            {"$set": {"urls": new_urls, "total_found": len(new_urls), "progress": progress,
                       "status": "complete" if progress >= 100 else "in-progress"}}
        )
    except Exception:
        pass
    # return latest
    latest = db["crawltask"].find_one({"_id": doc["_id"]})
    latest["_id"] = str(latest["_id"])
    return latest


@app.get("/api/crawl/urls")
def crawl_urls(task_id: str):
    latest = crawl_status(task_id)
    return {"urls": latest.get("urls", [])}


@app.post("/api/audit/start")
def start_audit(task_id: str):
    # create audit tasks for first 20 urls
    crawl = crawl_status(task_id)
    urls: List[str] = crawl.get("urls", [])[:20]
    created = []
    for u in urls:
        try:
            from schemas import AuditTask
            audit = AuditTask(crawl_id=task_id, url=u)
            created.append(create_document("audittask", audit))
        except Exception:
            continue
    return {"created": created, "count": len(created)}


@app.get("/api/audit/list")
def audit_list(task_id: str):
    tasks = get_documents("audittask", {"crawl_id": task_id})
    # simulate progression: update a few tasks per call
    updated = 0
    for t in tasks[:5]:
        if t.get("status") in {"complete", "error"}:
            continue
        url = t.get("url")
        try:
            score, report = run_basic_seo_checks(url)
            db["audittask"].update_one({"_id": t["_id"]}, {"$set": {
                "status": "complete",
                "score": score,
                "report": report
            }})
        except Exception as e:
            db["audittask"].update_one({"_id": t["_id"]}, {"$set": {
                "status": "error",
                "error": str(e)[:120]
            }})
        updated += 1
    tasks = get_documents("audittask", {"crawl_id": task_id})
    # serialize ids
    for t in tasks:
        t["_id"] = str(t["_id"]) if t.get("_id") else t.get("_id")
    return {"tasks": tasks}


@app.get("/api/audit/report")
def audit_report(audit_id: str):
    from bson import ObjectId
    try:
        t = db["audittask"].find_one({"_id": ObjectId(audit_id)})
        if not t:
            raise HTTPException(404, "Audit not found")
        t["_id"] = str(t["_id"]) if t.get("_id") else t.get("_id")
        return t
    except Exception:
        raise HTTPException(404, "Audit not found")


# Basic SEO checks used to produce a lightweight report

def run_basic_seo_checks(url: str):
    r = requests.get(url, timeout=8, headers={"User-Agent": "SEO-Audit-Bot/1.0"})
    html = r.text
    soup = BeautifulSoup(html, "html.parser")
    title = (soup.title.string.strip() if soup.title and soup.title.string else None)
    meta_desc = None
    for m in soup.find_all("meta"):
        if m.get("name", "").lower() == "description" and m.get("content"):
            meta_desc = m["content"]
            break
    h1 = soup.find("h1")
    images = soup.find_all("img")
    imgs_without_alt = [img.get("src") for img in images if not img.get("alt")]

    text = soup.get_text(" ")
    word_count = len([w for w in re.findall(r"\w+", text) if w])

    score = 100
    deductions = 0
    if not title:
        deductions += 20
    if not meta_desc:
        deductions += 15
    if not h1:
        deductions += 10
    if len(imgs_without_alt) > 0:
        deductions += min(15, len(imgs_without_alt))
    if word_count < 200:
        deductions += 10
    score = max(0, score - deductions)

    report = {
        "title": title,
        "meta_description": meta_desc,
        "has_h1": bool(h1),
        "image_count": len(images),
        "images_missing_alt": len(imgs_without_alt),
        "word_count": word_count,
        "recommendations": [
            *([] if title else ["Add a descriptive, keyword-rich <title> tag (50-60 chars)"]),
            *([] if meta_desc else ["Provide a compelling meta description (~155 chars)"]),
            *([] if h1 else ["Include a single clear H1 headline on the page"]),
            *([] if len(imgs_without_alt) == 0 else ["Add alt text to images for accessibility and SEO"]),
            *([] if word_count >= 200 else ["Increase on-page copy to at least 200-500 words"]),
        ],
    }
    return score, report


# Database diagnostic endpoint
@app.get("/test")
def test_database():
    response = {
        "backend": "✅ Running",
        "database": "❌ Not Available",
        "database_url": None,
        "database_name": None,
        "connection_status": "Not Connected",
        "collections": []
    }
    try:
        if db is not None:
            response["database"] = "✅ Available"
            response["database_url"] = "✅ Configured"
            response["database_name"] = db.name
            response["connection_status"] = "Connected"
            try:
                response["collections"] = db.list_collection_names()[:10]
                response["database"] = "✅ Connected & Working"
            except Exception as e:
                response["database"] = f"⚠️  Connected but Error: {str(e)[:50]}"
        else:
            response["database"] = "⚠️  Available but not initialized"
    except Exception as e:
        response["database"] = f"❌ Error: {str(e)[:50]}"

    import os
    response["database_url"] = "✅ Set" if os.getenv("DATABASE_URL") else "❌ Not Set"
    response["database_name"] = "✅ Set" if os.getenv("DATABASE_NAME") else "❌ Not Set"
    return response


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
