"""
File storage management with automatic cleanup
"""

import os
import shutil
import asyncio
import time
from pathlib import Path
from typing import Optional
import logging

logger = logging.getLogger(__name__)

# Storage configuration
DOWNLOADS_DIR = Path(os.getenv("DOWNLOADS_DIR", "/tmp/downloads"))
FILE_TTL_SECONDS = int(os.getenv("FILE_TTL_SECONDS", "300"))  # 5 minutes default
CLEANUP_INTERVAL_SECONDS = int(os.getenv("CLEANUP_INTERVAL_SECONDS", "60"))  # 1 minute


class StorageManager:
    """Manages downloaded files with automatic cleanup"""

    def __init__(self):
        self.downloads_dir = DOWNLOADS_DIR
        self.file_ttl = FILE_TTL_SECONDS
        self.cleanup_interval = CLEANUP_INTERVAL_SECONDS
        self._cleanup_task: Optional[asyncio.Task] = None
        self._init_storage()

    def _init_storage(self):
        """Initialize storage directory"""
        self.downloads_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"Storage initialized at {self.downloads_dir} (TTL: {self.file_ttl}s)")

    def get_job_dir(self, job_id: str) -> Path:
        """Get directory for a specific job"""
        job_dir = self.downloads_dir / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        return job_dir

    def get_download_path(self, job_id: str, filename: str = "video.mp4") -> Path:
        """Get full path for download file"""
        return self.get_job_dir(job_id) / filename

    def file_exists(self, job_id: str, filename: str = "video.mp4") -> bool:
        """Check if file exists"""
        path = self.get_download_path(job_id, filename)
        return path.exists() and path.is_file()

    def get_file_size(self, job_id: str, filename: str = "video.mp4") -> Optional[int]:
        """Get file size in bytes"""
        path = self.get_download_path(job_id, filename)
        if path.exists():
            return path.stat().st_size
        return None

    def get_file_age(self, job_id: str, filename: str = "video.mp4") -> Optional[float]:
        """Get file age in seconds"""
        path = self.get_download_path(job_id, filename)
        if path.exists():
            return time.time() - path.stat().st_mtime
        return None

    def delete_job_files(self, job_id: str):
        """Delete all files for a job"""
        job_dir = self.downloads_dir / job_id
        if job_dir.exists():
            try:
                shutil.rmtree(job_dir)
                logger.info(f"Deleted job files: {job_id}")
            except Exception as e:
                logger.error(f"Failed to delete job files {job_id}: {e}")

    def cleanup_old_files(self):
        """Remove files older than TTL"""
        if not self.downloads_dir.exists():
            return

        removed_count = 0
        removed_bytes = 0

        for job_dir in self.downloads_dir.iterdir():
            if not job_dir.is_dir():
                continue

            # Check all files in job directory
            should_delete = False
            for file_path in job_dir.rglob("*"):
                if not file_path.is_file():
                    continue

                age = time.time() - file_path.stat().st_mtime
                if age > self.file_ttl:
                    should_delete = True
                    break

            # Delete entire job directory if any file is expired
            if should_delete:
                try:
                    size = sum(f.stat().st_size for f in job_dir.rglob("*") if f.is_file())
                    shutil.rmtree(job_dir)
                    removed_count += 1
                    removed_bytes += size
                    logger.info(f"Cleaned up expired job: {job_dir.name} ({size / 1024 / 1024:.2f} MB)")
                except Exception as e:
                    logger.error(f"Failed to cleanup {job_dir.name}: {e}")

        if removed_count > 0:
            logger.info(f"Cleanup complete: {removed_count} jobs, {removed_bytes / 1024 / 1024:.2f} MB freed")

    def get_disk_usage(self) -> float:
        """Get disk usage percentage"""
        try:
            stat = shutil.disk_usage(self.downloads_dir)
            return (stat.used / stat.total) * 100
        except Exception as e:
            logger.error(f"Failed to get disk usage: {e}")
            return 0.0

    def get_total_size(self) -> int:
        """Get total size of all downloads in bytes"""
        if not self.downloads_dir.exists():
            return 0
        return sum(f.stat().st_size for f in self.downloads_dir.rglob("*") if f.is_file())

    async def start_cleanup_scheduler(self):
        """Start background cleanup task"""
        if self._cleanup_task is not None:
            logger.warning("Cleanup scheduler already running")
            return

        async def cleanup_loop():
            logger.info(f"Starting cleanup scheduler (interval: {self.cleanup_interval}s)")
            while True:
                try:
                    await asyncio.sleep(self.cleanup_interval)
                    self.cleanup_old_files()
                except asyncio.CancelledError:
                    logger.info("Cleanup scheduler cancelled")
                    break
                except Exception as e:
                    logger.error(f"Cleanup scheduler error: {e}")

        self._cleanup_task = asyncio.create_task(cleanup_loop())

    async def stop_cleanup_scheduler(self):
        """Stop background cleanup task"""
        if self._cleanup_task is not None:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
            self._cleanup_task = None
            logger.info("Cleanup scheduler stopped")


# Global storage manager instance
storage = StorageManager()
