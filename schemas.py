"""
Database Schemas for the AI SEO Audit SaaS

Each Pydantic model represents a MongoDB collection. The collection name
is the lowercase of the class name (handled by how we reference it).
"""
from pydantic import BaseModel, Field, HttpUrl
from typing import List, Optional, Literal
from datetime import datetime


class CrawlTask(BaseModel):
    seed_url: HttpUrl = Field(..., description="Seed URL to crawl")
    status: Literal["pending", "in-progress", "complete", "error"] = "pending"
    total_found: int = 0
    progress: int = 0  # 0-100
    urls: List[str] = []
    error: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class AuditTask(BaseModel):
    crawl_id: str = Field(..., description="Associated crawl task id")
    url: HttpUrl
    status: Literal["pending", "in-progress", "complete", "error"] = "pending"
    score: Optional[int] = None  # 0-100
    report: Optional[dict] = None
    error: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
