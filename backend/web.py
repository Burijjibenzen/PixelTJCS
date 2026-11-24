"""Serve the Godot Web export and its API from the same origin."""

from contextlib import asynccontextmanager
from pathlib import Path

from starlette.applications import Starlette
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.staticfiles import StaticFiles

from backend.api import app as api, get_service


WEB_DIR = Path(__file__).resolve().parents[1] / "build" / "web"


class WebAccess:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        scope["pixeltjcs.web"] = True
        if scope["type"] == "http":
            path = scope["path"].removeprefix(scope.get("root_path", ""))
            if (scope["method"] not in ("GET", "HEAD", "OPTIONS") and path.startswith("/api/")
                    and not path.startswith("/api/player/")
                    and path not in ("/api/world/report", "/api/world/observer-driver")):
                session = Headers(scope=scope).get("X-Player-Session")
                if not get_service().player.owns_session(session):
                    response = JSONResponse({"detail": "当前为旁观模式，请先加入玩家模式"}, status_code=403)
                    await response(scope, receive, send)
                    return
        await self.app(scope, receive, send)


class GameFiles(StaticFiles):
    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        if Path(path).suffix in ('', '.html', '.pck'):
            response.headers['Cache-Control'] = 'no-cache'
        return response


def create_app(web_dir: Path = WEB_DIR) -> Starlette:
    @asynccontextmanager
    async def lifespan(_app):
        for name in ("index.html", "index.js", "index.wasm", "index.pck"):
            if not (web_dir / name).is_file():
                raise RuntimeError(f"Missing {web_dir / name}. Export Godot's Web preset first.")
        async with api.router.lifespan_context(api):
            yield

    web = Starlette(lifespan=lifespan)

    web.add_middleware(WebAccess)
    web.mount("/api", api)
    web.mount("/", GameFiles(directory=web_dir, html=True, check_dir=False))
    return web


app = create_app()
