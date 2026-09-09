"""有限队列异步写入审计，连接池与清理任务跟随应用生命周期。"""

import asyncio
import logging
from datetime import datetime

import httpx
from sqlalchemy import update

from app.db.models import GatewayRequest, GatewayToken

logger = logging.getLogger(__name__)


class GatewayRuntime:
    def __init__(self, session_factory, client=None):
        self.sessions = session_factory
        self.client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10, read=300, write=60, pool=10),
            limits=httpx.Limits(max_connections=200, max_keepalive_connections=50),
            follow_redirects=False,
            trust_env=False,
        )
        # 每条正文最多 512 KiB，队列上限防止数据库故障拖垮推理链路。
        self.queue = asyncio.Queue(maxsize=128)
        self.dropped = 0
        self.worker = None
        self.cleaner = None

    async def start(self):
        self.worker = asyncio.create_task(self._write_loop())
        self.cleaner = asyncio.create_task(self._cleanup_loop())

    def record(self, record):
        try:
            self.queue.put_nowait(record)
        except asyncio.QueueFull:
            self.dropped += 1
            logger.error("模型网关审计队列已满，累计丢弃 %s 条", self.dropped)

    async def _write_loop(self):
        while True:
            first = await self.queue.get()
            batch = [first]
            while len(batch) < 32 and not self.queue.empty():
                batch.append(self.queue.get_nowait())
            try:
                async with self.sessions() as db:
                    db.add_all([GatewayRequest(**row) for row in batch])
                    latest = {}
                    for row in batch:
                        if row.get("token_id"):
                            key = row["token_id"]
                            latest[key] = max(latest.get(key, row["started_at"]), row["started_at"])
                    for token_id, timestamp in latest.items():
                        await db.execute(
                            update(GatewayToken)
                            .where(
                                GatewayToken.id == token_id,
                                (GatewayToken.last_used_at.is_(None))
                                | (GatewayToken.last_used_at < timestamp),
                            )
                            .values(last_used_at=timestamp)
                        )
                    await db.commit()
            except Exception:
                self.dropped += len(batch)
                logger.exception("模型网关审计写入失败，累计丢弃 %s 条", self.dropped)
            finally:
                for _ in batch:
                    self.queue.task_done()

    async def cleanup(self):
        async with self.sessions() as db:
            await db.execute(
                update(GatewayRequest)
                .where(
                    GatewayRequest.content_expires_at <= datetime.utcnow(),
                    GatewayRequest.content_state.in_(["available", "truncated"]),
                )
                .values(request_content=None, response_content=None, content_state="expired")
            )
            await db.commit()

    async def _cleanup_loop(self):
        while True:
            try:
                await self.cleanup()
            except Exception:
                logger.exception("模型网关过期正文清理失败")
            await asyncio.sleep(60)

    async def close(self):
        try:
            await asyncio.wait_for(self.queue.join(), timeout=15)
        except TimeoutError:
            logger.error("模型网关退出时仍有 %s 条审计待写入", self.queue.qsize())
        for task in (self.worker, self.cleaner):
            if task:
                task.cancel()
        await asyncio.gather(*(t for t in (self.worker, self.cleaner) if t), return_exceptions=True)
        await self.client.aclose()
