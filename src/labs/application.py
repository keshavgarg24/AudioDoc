"""FastAPI application factory.

Wiring only: logging, lifespan, middleware, error handlers, routers. All
behaviour lives in the packages this imports from.
"""
from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException

from .api.errors import (
    http_exception_handler,
    internal_error_handler,
    validation_handler,
)
from .api.v1.router import router as v1_router
from .core.config import get_settings
from .core.logging import configure as _configure_logging
from .core.logging import reset_request_id, set_request_id
from .core.metrics import record_request

# The container runs `uvicorn labs.application:app` directly, so the console
# entry point and its logging setup never execute. Without this call the root
# logger sits at its WARNING default and every log.info in the codebase is
# discarded: model load times, checkpoint provenance and warm-up completion
# all vanish, leaving no way to tell a healthy boot from a degraded one.
_configure_logging()

log = logging.getLogger(__name__)
settings = get_settings()

DESCRIPTION = """
Audio intelligence API: AI-generated music detection, full signal analysis and
a suite of focused measurement tools.

All analysis is asynchronous. Submit a file, receive a job id, then poll the
result or supply a webhook. A long-running job never holds an HTTP connection
open, which is what keeps the service working behind a load balancer.

Repeat submissions of byte-identical audio return the stored result rather
than re-running the pipeline.
""".strip()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Clear staged uploads a previous crash left behind.
    from .core.uploads import sweep_orphans
    sweep_orphans()

    # Persistence is optional; a no-op when LABS_MONGO_URI is unset.
    from .services.storage import get_mongo
    idx = get_mongo().ensure_indexes()
    if idx.get("enabled") and not idx.get("ok"):
        log.warning("Persistence configured but unreachable: %s", idx.get("error"))
    elif not idx.get("enabled"):
        log.warning(
            "LABS_MONGO_URI is not set. Running without persistence: results "
            "are not stored and identical audio is re-analysed on every "
            "submission.")

    # Compile librosa's JIT paths off the request path. The Dockerfile already
    # bakes the numba cache into the image, so with a correctly built image
    # this returns almost immediately; it is here so an image built without
    # that step degrades to a slow warm-up instead of a slow first caller.
    # Backgrounded because blocking would delay the first health check.
    from .core.warmup import warm_in_background
    warm_in_background()

    # Level 1 first, and unconditionally. Building the two ORT sessions parses
    # and plans both graphs, which is most of the tier's fixed cost, and it
    # takes well under a second - so there is no reason to make the first
    # caller pay it. It also means a deployment serving only the free tier
    # becomes useful immediately instead of after the 1.29 GB backbone lands.
    if settings.screen.enabled:
        from .screen import models as screen_models
        try:
            state = screen_models.warm(settings.screen)
            if not all(state.values()):
                log.warning("Level-1 models not fully available: %s", state)
        except Exception:
            log.warning("Level-1 warm-up failed; the screen tier will report "
                        "per-model errors per request.", exc_info=True)

    # Load weights once, before the first request is served.
    if settings.server.eager_load:
        try:
            from .ml.detector import get_detector

            get_detector(settings).load()
        except ImportError:
            # A screen-only image (Dockerfile --target screen) has no torch by
            # design. That is a valid deployment, not a failure: it serves
            # /v1/screen and nothing else, so say so once and carry on rather
            # than crash-looping a container that is working as configured.
            log.warning("Deep-tier dependencies are not installed; serving "
                        "the Level-1 screen only.")
        except Exception:
            log.error("Startup model load failed; /v1/ready will report not-ready.")

    yield

    # Let running analyses finish before the pool goes. Killing them mid-run
    # skips their cleanup: the staged upload stays on disk, the caller's
    # concurrency slot is never released, and anyone polling that job sees
    # `running` forever.
    from .services.jobs import shutdown as shutdown_jobs
    shutdown_jobs(settings.server.shutdown_drain_seconds)

    # Release the Level-1 ORT sessions explicitly, after the job pool has
    # drained and while the interpreter is still fully alive. Each session owns
    # native thread pools, and leaving them to be collected at interpreter
    # shutdown means their destructors run against a partially torn-down
    # runtime. Draining first matters: releasing a session an analysis is still
    # using would pull the graph out from under it.
    if settings.screen.enabled:
        from .screen import models as screen_models
        try:
            screen_models.reset()
        except Exception:
            log.debug("Level-1 session release failed", exc_info=True)


def create_app() -> FastAPI:
    expose_docs = settings.server.expose_docs
    app = FastAPI(
        title="LABS API",
        description=DESCRIPTION,
        version=settings.server.api_version,
        lifespan=lifespan,
        docs_url="/docs" if expose_docs else None,
        redoc_url="/redoc" if expose_docs else None,
        openapi_url="/openapi.json" if expose_docs else None,
    )

    # Response headers that are constant for every request. Built once rather
    # than per response.
    #
    # This is a JSON API that serves no HTML and embeds no third-party content,
    # so the browser-facing policy can be maximally restrictive: nothing is
    # allowed to load, frame, or be framed. The value of setting them here is
    # that /docs, /redoc and any error page rendered by a proxy inherit them
    # too, without a second place to remember.
    security_headers = {
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "no-referrer",
        "Cross-Origin-Opener-Policy": "same-origin",
        "Cross-Origin-Resource-Policy": "same-site",
        "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
    }
    if settings.server.hsts_seconds > 0:
        # Only meaningful over TLS, and actively harmful if the deployment is
        # ever reached over plain HTTP on a shared hostname, so it is opt-in.
        security_headers["Strict-Transport-Security"] = (
            f"max-age={settings.server.hsts_seconds}; includeSubDomains")

    @app.middleware("http")
    async def request_context(request, call_next):
        """Attach a request id to every response, echoing the caller's."""
        rid = request.headers.get("X-Request-Id") or uuid.uuid4().hex
        # Bounded and stripped of control characters before it is reflected:
        # the value goes straight into a response header, and an unbounded or
        # CR/LF-bearing one is header injection.
        rid = "".join(c for c in rid if c.isprintable() and c not in "\r\n")[:128]
        rid = rid or uuid.uuid4().hex
        request.state.request_id = rid
        token = set_request_id(rid)
        started = time.time()
        try:
            response = await call_next(request)
        finally:
            elapsed_ms = (time.time() - started) * 1000
            reset_request_id(token)

        record_request(request.url.path, response.status_code, elapsed_ms)
        response.headers["X-Request-Id"] = rid
        response.headers["X-Response-Time-ms"] = f"{elapsed_ms:.0f}"
        response.headers["X-API-Version"] = settings.server.api_version
        for header, value in security_headers.items():
            response.headers.setdefault(header, value)
        return response

    # Registered against Starlette's HTTPException, not FastAPI's subclass.
    # A request for an unrouted path never reaches a handler, so the 404 is
    # raised by the router itself as the base class and would otherwise be
    # served by Starlette's default handler as {"detail": "Not Found"} - the
    # one response in the API not wearing the documented error envelope, and
    # the one a client branching on error.code crashes on.
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_handler)
    app.add_exception_handler(500, internal_error_handler)

    if settings.server.cors_origins:
        origins = list(settings.server.cors_origins)
        wildcard = "*" in origins

        # Credentials are only offered to an explicit allow-list.
        #
        # Starlette does not send `Access-Control-Allow-Origin: *` when
        # credentials are enabled - it reflects the requesting origin instead,
        # because the spec forbids the literal wildcard alongside credentials.
        # The effect of "*" plus allow_credentials is therefore not "no CORS
        # restriction", it is "every origin is allow-listed, with credentials",
        # which lets any site on the internet make credentialed calls and read
        # the responses. Turning credentials off in that case keeps the open
        # deployment usable without handing it that property.
        if wildcard:
            log.warning(
                "LABS_CORS_ORIGINS is '*': credentials are disabled for CORS. "
                "Set it to your caller's exact origin in production.")

        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=not wildcard,
            allow_methods=["GET", "POST"],
            allow_headers=["*"],
            expose_headers=["X-Request-Id", "X-API-Version"],
        )

    app.include_router(v1_router)

    @app.get("/health", include_in_schema=False)
    def root_health():
        """Unversioned liveness probe.

        Load balancers and container orchestrators are configured once and
        outlive any API version, so the probe they point at must not carry a
        version in its path. Mirrors GET /v1/health.
        """
        from .api.v1.system import health
        return health()

    return app


app = create_app()
