"""Health, readiness, genre catalogue and usage."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse, PlainTextResponse

from ...artist import genres as g
from ...core.config import get_settings
from ...core.metrics import get_metrics
from ...services.jobs import get_store
from ..deps import require_scope

router = APIRouter(tags=["system"])
settings = get_settings()

# Imported rather than restated, so /v1/health cannot advertise a mode the
# submission route does not accept. These drifted apart once already.
from .analyses import MODES  # noqa: E402


def deep_installed() -> bool:
    """Whether the deep tier's dependencies exist in this image at all.

    A screen-only container (Dockerfile --target screen) has no torch by
    design, so this is a legitimate deployment state rather than a fault. It
    is distinct from "installed but not loaded yet", which is what
    `Detector.is_ready` reports, and the two must not be conflated: one is
    fixed for the life of the container and the other clears after ~10 s.
    """
    import importlib.util

    return importlib.util.find_spec("torch") is not None


def get_detector(settings=None):
    """The deep detector, or None when this image does not carry it.

    Imported inside the call, not at module scope, because `ml.detector` pulls
    torch, transformers and pytorch-lightning - about 2 GB of wheels. Level 1
    needs none of them, so deferring this is what makes the screen-only image
    possible: it serves /v1/screen and /v1/health with none of them installed.

    Returns None rather than raising, so every caller here has to decide what
    absence means for its own endpoint instead of propagating an ImportError
    to a client as a 500.
    """
    try:
        from ...ml.detector import get_detector as _get
    except ImportError:
        return None

    return _get(settings)


@router.get("/health", summary="Liveness and model state")
def health():
    """Always 200. Audio-only analysis works before weights are resident, so
    the service is useful while the model loads; read `model.ready` to tell.

    Deliberately thin. This endpoint is unauthenticated - a load balancer has
    to reach it - so it carries only what an anonymous caller legitimately
    needs: is the service up, and can it take an analysis yet.

    Everything an operator wants during an incident (checkpoint provenance,
    the S3 mirror URI, storage reachability, queue depth) is at
    GET /v1/diagnostics behind the admin scope. That split is not cosmetic:
    the fields removed from here named the weight repository, the model
    architecture and the storage bucket, which together describe how the
    detector is built and where its artifacts live.
    """
    d = get_detector(settings)
    # getattr throughout: a health endpoint must never 500.
    return JSONResponse(status_code=200, content={
        "status": "ok",
        "version": settings.server.api_version,
        "model": {
            # False for a screen-only image, and that is not a degradation:
            # `installed` distinguishes "this container will never serve the
            # deep tier" from "it will, once the weights land".
            "installed": d is not None,
            "ready": bool(d is not None and d.is_ready),
            # A caller needs to know whether a failure is theirs or ours, but
            # not what failed. `load_error` carried file paths and exception
            # text from the loader.
            "degraded": bool(d is not None and d.load_error),
        },
        # `depth` is work still outstanding, not everything the store is
        # holding. Terminal jobs stay in memory until their retention window
        # expires, so a total-based depth climbs to the store ceiling under
        # entirely healthy traffic and cannot be alerted on.
        "queue": _queue_health(),
        "modes": list(MODES),
        # Reported separately from `model` because the two tiers fail
        # independently: Level 1 can serve while the backbone is still loading,
        # which is the normal state of a cold container for its first 12 s.
        "screen": _screen_health(),
    })


def _screen_health() -> dict:
    """Level-1 readiness. Never raises; a health endpoint must not 500."""
    if not settings.screen.enabled:
        return {"enabled": False, "ready": False}
    try:
        from ...screen import models as screen_models

        state = screen_models.warm(settings.screen)
        return {"enabled": True, "ready": bool(state) and all(state.values()),
                "models": state}
    except Exception:
        return {"enabled": True, "ready": False}


def _queue_health() -> dict:
    stats = get_store().stats()
    return {
        "depth": stats.get("pending", 0),
        "queued": stats.get("queued", 0),
        "running": stats.get("running", 0),
        "retained": stats.get("total", 0),
    }


@router.get("/metrics", summary="Prometheus metrics (admin scope)",
            response_class=PlainTextResponse, include_in_schema=False)
def metrics(who=Depends(require_scope("admin"))):
    """Request rate, error rate, latency histogram, queue depth.

    Behind `admin` for the same reason /v1/diagnostics is: the route labels
    and call volumes describe how the service is used and how much of it
    there is. A scraper runs with its own key; point Prometheus at this with
    a `bearer_token`/`authorization` credential in the scrape config, or
    expose it only on an internal listener.
    """
    return PlainTextResponse(
        content=get_metrics().render(get_store().stats()),
        media_type="text/plain; version=0.0.4; charset=utf-8")


@router.get("/diagnostics", summary="Operator detail (admin scope)")
def diagnostics(who=Depends(require_scope("admin"))):
    """The detail that /health used to expose, for whoever is on call.

    Admin scope rather than merely authenticated: an `analyze` key belongs to
    an ordinary API consumer, and the model's provenance and the storage
    layout are not theirs to read.
    """
    from ...services.acrcloud import ACRConfig
    from ...services.storage import get_mongo

    d = get_detector(settings)
    info = getattr(d, "info", None)
    model = {
        # False for a screen-only image, and that is not a degradation:
        # `installed` distinguishes "this container will never serve the deep
        # tier" from "it will, once the weights land".
        "installed": d is not None,
        "ready": bool(d is not None and d.is_ready),
        "device": getattr(info, "device", None),
        "load_seconds": getattr(info, "load_seconds", None),
        "revision": getattr(info, "revision", None),
        "stage1_source": getattr(info, "stage1_source", None),
        "stage2_source": getattr(info, "stage2_source", None),
        "error": getattr(d, "load_error", None),
    }

    artifacts = None
    if d is not None:
        # Reports on the deep checkpoint cache, so it is meaningless - and its
        # import chain unavailable - in a screen-only image.
        from ...ml.checkpoints import cache_report

        artifacts = cache_report(settings.checkpoints)

    return JSONResponse(status_code=200, content={
        "status": "ok",
        "version": settings.server.api_version,
        "model": model,
        "screen": _screen_health(),
        "artifacts": artifacts,
        "verification": {"available": ACRConfig.from_env().configured},
        "storage": get_mongo().health(),
        "queue": get_store().stats(),
        "modes": list(MODES),
    })


@router.get("/ready", summary="Strict readiness for orchestrators")
def ready():
    """503 until this container can serve the tier it was deployed for.

    A screen-only worker (LABS_EAGER_LOAD=0, no deep scope issued) must not be
    held out of its target group waiting for a backbone it will never load, and
    a deep worker must not be added to one before its weights are resident.
    So readiness is judged against what is actually configured.
    """
    d = get_detector(settings)
    screen_ok = (not settings.screen.enabled) or _screen_health().get("ready")

    # A screen-only image is ready as soon as Level 1 is, whatever
    # LABS_EAGER_LOAD says: there is no backbone in it to wait for, and
    # holding the container out of its target group forever would be a
    # deployment that never comes up.
    if settings.server.eager_load and d is not None:
        if d.is_ready and screen_ok:
            return JSONResponse(status_code=200,
                                content={"ready": True, "tiers": ["screen", "deep"]})
        return JSONResponse(status_code=503, content={
            "ready": False, "screen_ready": bool(screen_ok),
            "deep_ready": d.is_ready,
            "error": {"code": "model_loading",
                      "message": "Model weights are not resident yet."}})

    if screen_ok:
        return JSONResponse(status_code=200,
                            content={"ready": True, "tiers": ["screen"]})
    return JSONResponse(status_code=503, content={
        "ready": False,
        "error": {"code": "screen_unavailable",
                  "message": "Level-1 models are not loadable."}})


@router.get("/genres", summary="Genres available for fit and transformation")
def genres():
    return {
        "count": len(g.GENRES),
        "genres": [
            {"key": k, "label": v["label"], "family": v["family"],
             "bpm": list(v["bpm"]), "target_lufs": v["lufs"],
             "defining_traits": v["signature"]}
            for k, v in sorted(g.GENRES.items())
        ],
    }


@router.get("/usage", summary="Usage for the calling key")
def usage(days: int = 30, who=Depends(require_scope("read"))):
    from ...api.v1.analyses import jsonable
    from ...services.storage import get_mongo

    if not who.id:
        return {"available": False,
                "reason": "Usage tracking requires a database-backed API key."}
    mongo = get_mongo()
    return JSONResponse(status_code=200, content=jsonable({
        "key": who.fingerprint, "name": who.name,
        "quota_per_day": who.quota_per_day or None,
        "used_today": mongo.usage_today(who.id),
        "daily": mongo.usage_summary(who.id, days=days),
    }))
