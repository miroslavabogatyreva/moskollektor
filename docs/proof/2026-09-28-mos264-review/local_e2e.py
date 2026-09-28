"""Disposable local PostGIS -> worker -> HTTP -> compiled UI, no API mocks.

Build frontend first. Set MOS184_TEST_DSN to a localhost PostGIS admin DSN.
Run with the project's development Python environment; node/npm are required.
Only the UUID-named test database is created and removed.
"""
import asyncio
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
import uvicorn

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "backend/tests")]
from test_sensor_risk_db import database


def main():
    fixture = database.__wrapped__()
    dsn = next(fixture)
    server = None
    thread = None
    try:
        os.environ["DATABASE_URL"] = dsn
        os.environ["AUTH_TRUST_HEADER"] = "1"
        from app.api.main import app
        from app.worker.sensor_scores import посчитать
        from fastapi.responses import FileResponse
        from starlette.staticfiles import StaticFiles

        async def prepare():
            import asyncpg
            conn = await asyncpg.connect(dsn)
            try:
                await conn.execute((ROOT / "db/seed/rbac.sql").read_text())
                print(await посчитать(conn), flush=True)
            finally:
                await conn.close()
        asyncio.run(prepare())
        dist = ROOT / "frontend/dist"
        assert (dist / "index.html").is_file(), "npm run build first"
        app.mount("/assets", StaticFiles(directory=dist / "assets"))

        @app.get("/{path:path}")
        async def frontend(path: str):
            return FileResponse(dist / "index.html")

        port = int(os.environ.get("MOS264_LOCAL_PORT", "18464"))
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{port}"
        for _ in range(100):
            try:
                if httpx.get(base + "/health", timeout=1).status_code == 200:
                    break
            except httpx.TransportError:
                pass
            time.sleep(.1)
        else:
            raise RuntimeError("local API did not start")
        env = dict(os.environ, BASE_URL=base, E2E_LOGIN="admin1", MOS264_LOCAL_E2E="1")
        subprocess.run(
            ["npx", "playwright", "test", "e2e/mos264-local.spec.ts", "--workers=1"],
            cwd=ROOT / "frontend", env=env, check=True,
        )
    finally:
        if server:
            server.should_exit = True
        if thread:
            thread.join(timeout=10)
        fixture.close()


if __name__ == "__main__":
    main()
