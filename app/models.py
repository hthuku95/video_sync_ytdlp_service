"""
Pydantic models for request/response schemas
"""

from pydantic import BaseModel, Field, HttpUrl
from typing import Optional, Dict, Any
from datetime import datetime
from enum import Enum


class ErrorCode(str, Enum):
    """Error code classifications"""
    VIDEO_UNAVAILABLE = "VIDEO_UNAVAILABLE"
    RATE_LIMITED = "RATE_LIMITED"
    DOWNLOAD_TIMEOUT = "DOWNLOAD_TIMEOUT"
    DISK_FULL = "DISK_FULL"
    INVALID_URL = "INVALID_URL"
    NETWORK_ERROR = "NETWORK_ERROR"
    SERVER_ERROR = "SERVER_ERROR"


class DownloadRequest(BaseModel):
    """Request schema for /api/v1/download"""
    video_url: str = Field(..., description="YouTube video URL")
    job_id: Optional[str] = Field(None, description="Optional job ID for tracking")
    quality: Optional[str] = Field("720p", description="Video quality: 360p, 480p, 720p, 1080p, best")
    format: Optional[str] = Field("mp4", description="Video format: mp4, webm, mkv")
    prefer_base64: Optional[bool] = Field(False, description="Force base64 encoding instead of URL")
    timeout_seconds: Optional[int] = Field(3600, description="Download timeout in seconds")
    only_strategy: Optional[int] = Field(None, description="Run only this strategy number (1-based). Use GET /api/v1/strategies to list available strategies.")

    class Config:
        json_schema_extra = {
            "example": {
                "video_url": "https://youtube.com/watch?v=dQw4w9WgXcQ",
                "job_id": "123",
                "quality": "720p",
                "format": "mp4",
                "prefer_base64": False,
                "timeout_seconds": 3600
            }
        }


class VideoMetadata(BaseModel):
    """Video metadata included in responses"""
    title: str
    duration_seconds: float
    width: Optional[int] = None
    height: Optional[int] = None
    file_size_bytes: Optional[int] = None
    format: str
    video_id: Optional[str] = None
    channel_id: Optional[str] = None
    channel_name: Optional[str] = None
    upload_date: Optional[str] = None
    view_count: Optional[int] = None
    like_count: Optional[int] = None
    is_live: Optional[bool] = False
    is_private: Optional[bool] = False


class ErrorDetail(BaseModel):
    """Error details"""
    code: ErrorCode
    message: str
    is_transient: bool = Field(..., description="True if retry might succeed, False if permanent")
    retry_after_seconds: Optional[int] = None
    details: Optional[Dict[str, Any]] = None


class DownloadResponse(BaseModel):
    """Success response for /api/v1/download"""
    success: bool = True
    method: str = Field(..., description="'url' or 'base64'")
    download_url: Optional[str] = Field(None, description="URL to download file (method=url)")
    file_data: Optional[str] = Field(None, description="Base64-encoded file data (method=base64)")
    expires_at: Optional[datetime] = Field(None, description="URL expiration time (method=url)")
    metadata: VideoMetadata


class ErrorResponse(BaseModel):
    """Error response for failed downloads"""
    success: bool = False
    error: ErrorDetail


class InfoRequest(BaseModel):
    """Request schema for /api/v1/info"""
    video_url: str = Field(..., description="YouTube video URL")
    include_formats: Optional[bool] = Field(False, description="Include available format list")

    class Config:
        json_schema_extra = {
            "example": {
                "video_url": "https://youtube.com/watch?v=dQw4w9WgXcQ",
                "include_formats": False
            }
        }


class InfoResponse(BaseModel):
    """Response schema for /api/v1/info"""
    success: bool = True
    metadata: VideoMetadata


class HealthStats(BaseModel):
    """Statistics for health check"""
    total_downloads: int
    active_downloads: int
    failed_downloads: int
    disk_usage_percent: float


class HealthResponse(BaseModel):
    """Response schema for /api/v1/health"""
    status: str
    version: str
    uptime_seconds: float
    stats: HealthStats
    yt_dlp_version: str
