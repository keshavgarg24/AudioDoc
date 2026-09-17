"""MongoDB persistence and S3 audio storage.

Storage model
-------------
A full report is 200 KB to 2 MB of JSON, most of it numeric series that exist
only to draw charts: the self-similarity matrix, spectrum curves, RMS and BPM
series, per-beat deviation histograms. Storing those verbatim, per analysis,
is what turns a working database into an expensive one.

So each analysis is written as three tiers:

  tier 1  flat scalars at the top of the document, indexed and queryable.
          "every AI-flagged trap track above 130 BPM last month" is an index
          scan, never a scan of compressed blobs.

  tier 2  the trimmed report, zlib-compressed into a single binary field.
          Heavy numeric series are dropped first (see `_HEAVY_PATHS` and the
          generic long-numeric-array rule). Typically 200 KB to 1 MB of JSON
          becomes 15 to 60 KB stored.

  tier 3  the audio itself, in S3, referenced by key. Never in MongoDB.
          Off by default; enable with LABS_STORE_AUDIO=true.

Everything degrades: with no LABS_MONGO_URI the service runs exactly as it did
before, in memory, writing nothing.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import zlib
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from ..core.config import StorageConfig

log = logging.getLogger(__name__)

# Report paths dropped before compression. These are chart-drawing series with
# no analytical value once the chart has been rendered once.
_HEAVY_PATHS: Tuple[Tuple[str, ...], ...] = (
    ("structure",),
    ("features", "spectrum"),
    ("features", "spectral", "centroid_series"),
    ("features", "dynamics", "rms_series"),
    ("features", "tonal", "chroma"),
    ("musical", "rhythm", "bpm_curve"),
    ("musical", "rhythm", "beat_times"),
    ("musical", "groove", "deviation_histogram"),
    ("musical", "harmony", "chroma"),
    ("production", "loudness", "short_term_series"),
)

# Any list longer than this made only of numbers is a plotting series.
_MAX_NUMERIC_ARRAY = 64


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


def _expires_at(seconds: float) -> Optional[datetime]:
    """When a document should expire, or None to keep it forever.

    A MongoDB TTL index deletes a document once `expires_at` is in the past,
    and IGNORES documents whose `expires_at` is missing or not a date. That is
    what makes 0 work as "keep forever" without needing a second collection or
    a conditional index: the index stays in place, and a document simply never
    becomes eligible for it.

    The practical consequence is that retention can be changed per-document and
    at runtime. Turning a TTL on later expires only what is written after the
    change; already-stored records keep their absent `expires_at` and survive.
    """
    return _utcnow() + timedelta(seconds=seconds) if seconds and seconds > 0 else None


def _with_expiry(doc: Dict, seconds: float) -> Dict:
    """Add `expires_at` to `doc` only when a finite retention is configured."""
    at = _expires_at(seconds)
    if at is not None:
        doc["expires_at"] = at
    return doc


# --------------------------------------------------------------------------
# report trimming and compression
# --------------------------------------------------------------------------
def _is_numeric_series(v: Any) -> bool:
    if not isinstance(v, list) or len(v) <= _MAX_NUMERIC_ARRAY:
        return False
    return all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in v)


def _strip(node: Any) -> Any:
    """Recursively drop long numeric arrays anywhere in the tree."""
    if isinstance(node, dict):
        return {k: _strip(v) for k, v in node.items() if not _is_numeric_series(v)}
    if isinstance(node, list):
        if _is_numeric_series(node):
            return []
        return [_strip(v) for v in node]
    return node


def trim_report(report: Dict) -> Tuple[Dict, List[str]]:
    """Remove plotting series. Returns (trimmed, list of dropped paths)."""
    out = json.loads(json.dumps(report, default=str))
    dropped: List[str] = []

    for path in _HEAVY_PATHS:
        cur = out
        for key in path[:-1]:
            cur = cur.get(key) if isinstance(cur, dict) else None
            if cur is None:
                break
        if isinstance(cur, dict) and path[-1] in cur:
            cur.pop(path[-1], None)
            dropped.append(".".join(path))

    out = _strip(out)
    return out, dropped


def compress_report(report: Dict) -> Dict:
    """Trim, serialise and compress. Returns the fields to store."""
    trimmed, dropped = trim_report(report)
    raw = json.dumps(trimmed, separators=(",", ":"), default=str).encode("utf-8")
    packed = zlib.compress(raw, level=6)
    return {
        "report_gz": packed,
        "report_bytes_raw": len(raw),
        "report_bytes_gz": len(packed),
        "report_dropped": dropped,
        "report_encoding": "zlib+json",
    }


def decompress_report(doc: Dict) -> Optional[Dict]:
    blob = doc.get("report_gz")
    if not blob:
        return None
    try:
        return json.loads(zlib.decompress(bytes(blob)).decode("utf-8"))
    except Exception:
        log.exception("Could not decompress stored report")
        return None


# --------------------------------------------------------------------------
# tier-1 summary extraction
# --------------------------------------------------------------------------
def _g(d: Optional[Dict], *path, default=None):
    cur = d or {}
    for p in path:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(p)
        if cur is None:
            return default
    return cur


def summarize(report: Dict) -> Dict:
    """The flat, indexed view of one analysis."""
    det = report.get("detection") or {}
    consensus = det.get("consensus") or {}
    verification = det.get("verification") or {}

    is_ai: Optional[bool] = None
    if consensus.get("verdict") in ("ai_generated", "human"):
        is_ai = consensus["verdict"] == "ai_generated"
    elif report.get("prediction"):
        is_ai = report["prediction"] == "Fake"

    artist = report.get("artist") or {}

    return {
        "mode": report.get("mode"),
        "duration_s": _g(report, "source", "duration_seconds"),
        "verdict": {
            "prediction": report.get("prediction"),
            "is_ai": is_ai,
            "confidence": report.get("confidence"),
            "fake_probability": report.get("fake_probability"),
            "raw_logit": report.get("raw_logit"),
            "reliability_score": _g(report, "reliability", "score"),
            "decided_by": (
                "verification" if verification.get("prediction") else "primary"),
            "consensus": consensus.get("agreement"),
            "verification_available": bool(verification.get("prediction")),
            "likely_source": verification.get("likely_source"),
        },
        "music": {
            "bpm": _g(report, "musical", "rhythm", "bpm"),
            "key": _g(report, "musical", "harmony", "key"),
            "camelot": _g(report, "musical", "harmony", "camelot"),
            "time_signature": _g(report, "musical", "rhythm", "time_signature"),
            "lufs": _g(report, "production", "loudness", "integrated_lufs"),
            "true_peak": _g(report, "production", "loudness", "true_peak_dbtp"),
            "lra": _g(report, "production", "loudness", "loudness_range_lu"),
            "width_pct": _g(report, "production", "stereo", "width_pct"),
            "energy": _g(report, "industry", "catalogue_features", "energy"),
            "danceability": _g(report, "industry", "catalogue_features", "danceability"),
            "valence": _g(report, "industry", "catalogue_features", "valence"),
            "instrumentalness": _g(report, "industry", "catalogue_features", "instrumentalness"),
            "section_count": _g(report, "musical", "arrangement", "section_count"),
            "intro_s": _g(report, "industry", "structure", "intro_length_s"),
        },
        "artist": {
            "hit_score": _g(artist, "hit_potential", "score"),
            "hit_grade": _g(artist, "hit_potential", "grade"),
            "primary_genre": _g(artist, "genre_fit", "primary", "genre"),
            "genre_label": _g(artist, "genre_fit", "primary", "label"),
            "genre_match": _g(artist, "genre_fit", "primary", "match"),
            "release_status": _g(artist, "release_readiness", "status"),
            "sync_score": _g(artist, "sync_readiness", "score"),
        },
    }


# --------------------------------------------------------------------------
# S3 audio
# --------------------------------------------------------------------------
class AudioStore:
    """Puts analysed audio in S3. Never fatal."""

    def __init__(self, cfg: StorageConfig):
        self.cfg = cfg
        self._client = None

    @property
    def enabled(self) -> bool:
        return bool(self.cfg.store_audio and self.cfg.audio_bucket)

    def _s3(self):
        if self._client is None:
            import boto3
            self._client = boto3.client("s3", region_name=self.cfg.s3_region or None)
        return self._client

    def put(self, path: str, analysis_id: str, filename: str) -> Optional[Dict]:
        if not self.enabled:
            return None
        try:
            with open(path, "rb") as fh:
                digest = hashlib.sha256()
                for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                    digest.update(chunk)
            ext = os.path.splitext(filename)[1].lower() or ".bin"
            day = _utcnow().strftime("%Y/%m/%d")
            key = f"{self.cfg.audio_prefix}/{day}/{analysis_id}{ext}"
            self._s3().upload_file(path, self.cfg.audio_bucket, key)
            return {
                "bucket": self.cfg.audio_bucket, "key": key,
                "sha256": digest.hexdigest(),
                "bytes": os.path.getsize(path),
                "stored_at": _utcnow(),
            }
        except Exception:
            log.warning("Audio upload failed for %s", analysis_id, exc_info=True)
            return None

    def presigned_get(self, bucket: str, key: str, ttl: int = 900) -> Optional[str]:
        try:
            return self._s3().generate_presigned_url(
                "get_object", Params={"Bucket": bucket, "Key": key},
                ExpiresIn=ttl)
        except Exception:
            log.warning("Presign failed for %s/%s", bucket, key, exc_info=True)
            return None


# --------------------------------------------------------------------------
# MongoDB
# --------------------------------------------------------------------------
class MongoStore:
    """Analyses, API keys, usage counters and idempotency records."""

    def __init__(self, cfg: StorageConfig):
        self.cfg = cfg
        self._db = None
        self._ready = False
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return bool(self.cfg.mongo_uri)

    def db(self):
        if not self.enabled:
            return None
        if self._db is None:
            with self._lock:
                if self._db is None:
                    from pymongo import MongoClient
                    client = MongoClient(
                        self.cfg.mongo_uri,
                        serverSelectionTimeoutMS=5000,
                        connectTimeoutMS=5000,
                        # Without this a half-open socket blocks the calling
                        # worker thread forever. Every call site treats a
                        # storage failure as non-fatal, but only if it returns:
                        # a hang takes an analysis worker out of the pool for
                        # the life of the process.
                        socketTimeoutMS=10000,
                        # Bounded so a burst cannot open connections faster
                        # than the server will accept them; waitQueueTimeout
                        # turns pool exhaustion into a prompt error rather
                        # than an unbounded wait.
                        maxPoolSize=50,
                        minPoolSize=2,
                        waitQueueTimeoutMS=5000,
                        retryWrites=True,
                        # Read from the primary, not from a secondary.
                        #
                        # On a replica set the default lets a read land on a
                        # lagging secondary, and job status is the one thing
                        # that cannot tolerate it: a client polling
                        # GET /v1/analyses/{id} can be told `succeeded` and
                        # then `queued` on the next request, because the two
                        # reads hit different members. Callers reasonably treat
                        # that as the job restarting.
                        #
                        # These are small, indexed, single-document reads on a
                        # low-traffic collection, so there is nothing to gain
                        # from spreading them and a correctness bug to lose.
                        readPreference="primary",
                        appname="labs-api",
                    )
                    self._db = client[self.cfg.mongo_db]
        return self._db

    def ensure_indexes(self) -> Dict:
        """Idempotent. Called once at startup."""
        if not self.enabled:
            return {"enabled": False}
        try:
            from pymongo import ASCENDING, DESCENDING
            db = self.db()

            db.analyses.create_index([("created_at", DESCENDING)])
            db.analyses.create_index([("api_key_id", ASCENDING),
                                      ("created_at", DESCENDING)])
            db.analyses.create_index([("verdict.is_ai", ASCENDING),
                                      ("created_at", DESCENDING)])
            db.analyses.create_index([("artist.primary_genre", ASCENDING)])
            db.analyses.create_index([("audio.sha256", ASCENDING)])

            # Every TTL index below is INERT BY DEFAULT.
            #
            # It deletes a document only once that document carries an
            # `expires_at` in the past, and the writers only set that field
            # when a positive retention is configured (see `_with_expiry`).
            # With the defaults - LABS_RESULT_TTL_DAYS=0, LABS_AUDIO_TTL_DAYS=0,
            # LABS_SCREEN_RETAIN_S=0 - nothing here ever expires.
            #
            # The index is still created, because creating it later on a large
            # collection is an expensive online build, and because retention
            # then becomes a config change rather than a migration.
            db.analyses.create_index(
                [("expires_at", ASCENDING)], expireAfterSeconds=0)

            db.api_keys.create_index([("key_hash", ASCENDING)], unique=True)
            db.api_keys.create_index([("active", ASCENDING)])

            db.usage.create_index([("api_key_id", ASCENDING), ("day", ASCENDING)],
                                  unique=True)

            db.idempotency.create_index([("key", ASCENDING)], unique=True)
            db.idempotency.create_index([("expires_at", ASCENDING)],
                                        expireAfterSeconds=0)

            # Dedup lookups hit (sha256, mode) together, so the compound index
            # is what actually serves them; the sha256-only index above still
            # covers cross-mode lookups.
            db.analyses.create_index([("audio.sha256", ASCENDING),
                                      ("mode", ASCENDING),
                                      ("created_at", DESCENDING)])

            db.tool_results.create_index([("tool", ASCENDING),
                                          ("created_at", DESCENDING)])
            db.tool_results.create_index([("expires_at", ASCENDING)],
                                         expireAfterSeconds=0)

            # Reverse lookup: every cached tool result touching one audio file.
            db.tool_results.create_index([("input_digests", ASCENDING),
                                          ("created_at", DESCENDING)])

            # Distributed job state. Only used when LABS_SQS_QUEUE_URL is set:
            # the queue carries the WORK and this collection carries the
            # STATE, so any API container can report on a job any worker in
            # the fleet is running. Without it a submission accepted by one
            # container and finished by another is unreportable.
            # Retained Level-1 results, so a caller who was cut short by an
            # early exit can appeal without re-uploading. Kept permanently by
            # default: a screen plus the appeal it attracts is one labelled
            # example of the free tier being wrong, which is the only such data
            # the system produces.
            db.screens.create_index([("created_at", DESCENDING)])
            db.screens.create_index([("expires_at", ASCENDING)],
                                    expireAfterSeconds=0)
            db.screens.create_index([("sha256", ASCENDING)])

            # The human feedback loop. One document per appeal or correction,
            # carrying what the machine said and what the human said about it.
            # This is the only labelled data the system generates about its own
            # mistakes, so it outlives the audio deliberately.
            db.feedback.create_index([("created_at", DESCENDING)])
            db.feedback.create_index([("kind", ASCENDING),
                                      ("created_at", DESCENDING)])
            db.feedback.create_index([("analysis_id", ASCENDING)])
            db.feedback.create_index([("screen_id", ASCENDING)])
            db.feedback.create_index([("outcome", ASCENDING)])

            db.jobs.create_index([("created_at", DESCENDING)])
            db.jobs.create_index([("api_key_id", ASCENDING),
                                  ("created_at", DESCENDING)])
            db.jobs.create_index([("status", ASCENDING)])
            # Serves count_active_jobs, which runs on every distributed
            # submission. Without the compound index that count scans every
            # job the key has ever created, so it gets slower for exactly the
            # heavy users it exists to bound.
            db.jobs.create_index([("api_key_id", ASCENDING),
                                  ("status", ASCENDING)])
            db.jobs.create_index([("expires_at", ASCENDING)],
                                 expireAfterSeconds=0)

            # One record per unique audio file, keyed by SHA-256.
            # Lets any tool check whether this audio was seen before.
            db.audio_files.create_index([("sha256", ASCENDING)], unique=True)
            db.audio_files.create_index([("last_seen_at", DESCENDING)])
            db.audio_files.create_index([("analysis_count", DESCENDING)])


            self._ready = True
            log.info("MongoDB indexes ready on '%s'", self.cfg.mongo_db)
            return {"enabled": True, "ok": True, "database": self.cfg.mongo_db}
        except Exception as exc:
            log.warning("MongoDB unavailable (%s); running without persistence", exc)
            return {"enabled": True, "ok": False, "error": str(exc)}

    # -- distributed job state ----------------------------------------------
    def create_job(self, job_id: str, meta: Dict,
                   api_key_id: Optional[str] = None,
                   ttl_seconds: int = 86400) -> bool:
        """Record a queued job so it is visible before any worker takes it.

        Written BEFORE the message is published. The other order leaves a
        window where a worker can finish the analysis and update a document
        that does not exist yet, so the upsert in `update_job` recreates it
        without the owner and the submitter is locked out of their own result.
        """
        if not self.enabled:
            return False
        try:
            self.db().jobs.insert_one(_with_expiry({
                "_id": job_id,
                "status": "queued",
                "progress": "queued",
                "created_at": _utcnow(),
                "started_at": None,
                "finished_at": None,
                "api_key_id": api_key_id,
                **meta,
            }, ttl_seconds))
            return True
        except Exception:
            log.warning("Could not create job record %s", job_id, exc_info=True)
            return False

    def update_job(self, job_id: str, **fields) -> bool:
        if not self.enabled:
            return False
        try:
            self.db().jobs.update_one({"_id": job_id}, {"$set": fields})
            return True
        except Exception:
            log.warning("Could not update job %s", job_id, exc_info=True)
            return False

    def get_job(self, job_id: str) -> Optional[Dict]:
        if not self.enabled:
            return None
        try:
            return self.db().jobs.find_one({"_id": job_id})
        except Exception:
            log.debug("Job lookup failed for %s", job_id, exc_info=True)
            return None

    def count_active_jobs(self, api_key_id: str) -> Optional[int]:
        """How many jobs this key currently has queued or running, fleet-wide.

        The in-process limiter cannot answer this. It counts what THIS
        container admitted, so behind a load balancer the real cap is the
        configured one multiplied by the number of API containers, and the
        distributed path releases its slot the moment the job is handed to
        SQS - by design, since holding it would cap a key across the whole
        fleet at a number meant to bound one process. The net effect is that
        nothing bounds how much queue one key can occupy.

        Counting the shared job collection is the only answer that is true for
        the deployment rather than for one replica. Returns None when the store
        is unavailable, and callers must treat that as "do not know" and admit
        the request: a database blip must not become a service outage.
        """
        if not self.enabled:
            return None
        try:
            return self.db().jobs.count_documents(
                {"api_key_id": api_key_id,
                 "status": {"$in": ["queued", "running"]}})
        except Exception:
            log.debug("Active-job count failed for %s", api_key_id, exc_info=True)
            return None

    def job_is_terminal(self, job_id: str) -> bool:
        """Guard against SQS at-least-once redelivery.

        SQS can deliver the same message twice - a visibility timeout that
        expired while a slow analysis was still running, or an at-least-once
        redelivery after a failed delete. Re-running a finished job would cost
        another 30-90 s of CPU for a result already stored, so the worker
        checks this first.
        """
        doc = self.get_job(job_id)
        return bool(doc and doc.get("status") in ("succeeded", "failed",
                                                  "cancelled"))

    # -- retained Level-1 results -------------------------------------------
    def save_screen(self, screen_id: str, result: Dict, audio: Optional[Dict],
                    meta: Dict, ttl_seconds: int) -> bool:
        """Retain one Level-1 result so it can be appealed.

        Only called when Level 1 ENDED a request on its own. That is the case
        where the caller has no deep result and might reasonably want one, and
        it is a small fraction of traffic - every other path already escalates
        to a stored analysis, so retaining them here would duplicate rather
        than preserve.

        `ttl_seconds` of 0 keeps the record permanently, which is the default.
        """
        if not self.enabled:
            return False
        try:
            self.db().screens.insert_one(_with_expiry({
                "_id": screen_id,
                "created_at": _utcnow(),
                "result": result,
                "audio": audio,
                "sha256": (audio or {}).get("sha256"),
                "escalated": False,
                **meta,
            }, ttl_seconds))
            return True
        except Exception:
            log.warning("Could not retain screen %s", screen_id, exc_info=True)
            return False

    def get_screen(self, screen_id: str) -> Optional[Dict]:
        if not self.enabled:
            return None
        try:
            return self.db().screens.find_one({"_id": screen_id})
        except Exception:
            log.debug("Screen lookup failed for %s", screen_id, exc_info=True)
            return None

    def mark_screen_escalated(self, screen_id: str, analysis_id: str) -> bool:
        if not self.enabled:
            return False
        try:
            self.db().screens.update_one(
                {"_id": screen_id},
                {"$set": {"escalated": True, "analysis_id": analysis_id,
                          "escalated_at": _utcnow()}})
            return True
        except Exception:
            log.debug("Could not mark screen %s escalated", screen_id,
                      exc_info=True)
            return False

    # -- human feedback -----------------------------------------------------
    def save_feedback(self, doc: Dict) -> Optional[str]:
        """Record one human judgement about a machine verdict.

        Never fails a request. A lost feedback row is a lost data point; a
        failed request over one is a lost user.
        """
        if not self.enabled:
            return None
        try:
            doc = {"created_at": _utcnow(), **doc}
            return str(self.db().feedback.insert_one(doc).inserted_id)
        except Exception:
            log.warning("Could not record feedback", exc_info=True)
            return None

    def resolve_feedback(self, screen_id: str, outcome: str,
                         deep: Dict) -> bool:
        """Close the loop: attach what Level 2 concluded to the appeal."""
        if not self.enabled:
            return False
        try:
            self.db().feedback.update_many(
                {"screen_id": screen_id, "outcome": "pending"},
                {"$set": {"outcome": outcome, "deep": deep,
                          "resolved_at": _utcnow()}})
            return True
        except Exception:
            log.debug("Could not resolve feedback for %s", screen_id,
                      exc_info=True)
            return False

    def feedback_summary(self, days: int = 30) -> Dict:
        """How often humans disagree, and how often they turn out to be right.

        The second number is the one that matters: an appeal rate is a UX
        signal, but an OVERTURN rate is a measurement of the detector.
        """
        if not self.enabled:
            return {"enabled": False}
        try:
            from datetime import timedelta

            since = _utcnow() - timedelta(days=days)
            rows = list(self.db().feedback.aggregate([
                {"$match": {"created_at": {"$gte": since}}},
                {"$group": {"_id": {"kind": "$kind", "outcome": "$outcome"},
                            "n": {"$sum": 1}}},
            ]))
            counts: Dict = {}
            for r in rows:
                kind = r["_id"].get("kind") or "unknown"
                outcome = r["_id"].get("outcome") or "unknown"
                counts.setdefault(kind, {})[outcome] = r["n"]

            appeals = counts.get("escalation", {})
            overturned = appeals.get("overturned", 0)
            upheld = appeals.get("upheld", 0)
            decided = overturned + upheld
            return {
                "enabled": True,
                "days": days,
                "counts": counts,
                # None, not 0.0, when nothing has been decided. A rate computed
                # from zero samples is not a rate, and reporting it as 0%
                # reads as "the detector is never wrong".
                "overturn_rate": (round(overturned / decided, 4)
                                  if decided else None),
                "decided": decided,
            }
        except Exception:
            log.warning("Feedback summary failed", exc_info=True)
            return {"enabled": True, "error": "unavailable"}

    # -- analyses -----------------------------------------------------------
    def save_analysis(self, analysis_id: str, report: Dict, meta: Dict,
                      audio: Optional[Dict] = None) -> bool:
        if not self.enabled:
            return False
        try:
            doc = _with_expiry({
                "_id": analysis_id,
                "created_at": _utcnow(),
                **summarize(report),
                **compress_report(report),
                "filename": meta.get("filename"),
                "reference": meta.get("reference"),
                "api_key_id": meta.get("api_key_id"),
                "request_id": meta.get("request_id"),
                "model": meta.get("model") or {},
                "timing": meta.get("timing") or {},
            }, self.cfg.result_ttl_days * 86400)
            if audio:
                doc["audio"] = audio
            self.db().analyses.replace_one({"_id": analysis_id}, doc, upsert=True)
            log.info("Stored analysis %s (%d KB compressed from %d KB)",
                     analysis_id, doc["report_bytes_gz"] // 1024,
                     doc["report_bytes_raw"] // 1024)
            return True
        except Exception:
            log.warning("Could not store analysis %s", analysis_id, exc_info=True)
            return False

    def get_analysis(self, analysis_id: str,
                     full: bool = True) -> Optional[Dict]:
        if not self.enabled:
            return None
        try:
            doc = self.db().analyses.find_one({"_id": analysis_id})
            if not doc:
                return None
            out = {k: v for k, v in doc.items()
                   if k not in ("report_gz", "expires_at")}
            if full:
                out["report"] = decompress_report(doc)
            return out
        except Exception:
            log.warning("Could not read analysis %s", analysis_id, exc_info=True)
            return None

    def list_analyses(self, api_key_id: Optional[str] = None,
                      limit: int = 50, skip: int = 0,
                      **filters) -> List[Dict]:
        if not self.enabled:
            return []
        try:
            from pymongo import DESCENDING
            q: Dict = {}
            if api_key_id:
                q["api_key_id"] = api_key_id
            q.update({k: v for k, v in filters.items() if v is not None})
            cur = (self.db().analyses.find(q, {"report_gz": 0})
                   .sort("created_at", DESCENDING).skip(skip).limit(min(limit, 200)))
            return list(cur)
        except Exception:
            log.warning("Could not list analyses", exc_info=True)
            return []

    def find_by_hash(self, sha256: str, mode: Optional[str] = None) -> Optional[Dict]:
        """Same audio analysed before? Lets callers skip a re-run.

        `mode` matters: an "audio" run carries no verdict, so returning one to
        a caller who asked for "ai" would answer a different question than the
        one asked. When mode is given only a matching run is a hit.
        """
        if not self.enabled:
            return None
        try:
            q: Dict = {"audio.sha256": sha256}
            if mode:
                q["mode"] = mode
            return self.db().analyses.find_one(
                q, {"report_gz": 0}, sort=[("created_at", -1)])
        except Exception:
            return None

    def find_report_by_hash(self, sha256: str,
                            mode: Optional[str] = None,
                            api_key_id: Optional[str] = None) -> Optional[Dict]:
        """The full stored report for previously analysed audio.

        Separate from find_by_hash because that one deliberately excludes the
        compressed blob for listing purposes, and a dedup hit needs it.

        `api_key_id` scopes the lookup to one caller and is REQUIRED for the
        dedup cache. Without it the hash is a global lookup key: anyone holding
        bytes that someone else already analysed gets their stored report back
        in full, which is a cross-tenant disclosure rather than a cache hit. It
        also breaks the caller, because the `_id` returned belongs to the other
        key's job and every later poll of it fails the ownership check in
        `get_analysis` with a 404 - the job looks permanently stuck even though
        the analysis succeeded for its actual owner.
        """
        if not self.enabled:
            return None
        try:
            q: Dict = {"audio.sha256": sha256}
            if mode:
                q["mode"] = mode
            if api_key_id:
                q["api_key_id"] = api_key_id
            doc = self.db().analyses.find_one(q, sort=[("created_at", -1)])
            if not doc:
                return None
            report = decompress_report(doc)
            if report is None:
                return None
            doc.pop("report_gz", None)
            doc["report"] = report
            return doc
        except Exception:
            log.debug("Dedup report read failed", exc_info=True)
            return None

    # -- audio files --------------------------------------------------------
    def get_audio_file(self, sha256: str) -> Optional[Dict]:
        """Metadata for a previously analysed audio file, by SHA-256."""
        if not self.enabled or not sha256:
            return None
        try:
            return self.db().audio_files.find_one({"_id": sha256},
                                                   {"_id": 0})
        except Exception:
            return None

    def list_tool_results_for_audio(self, sha256: str,
                                    limit: int = 20) -> List[Dict]:
        """Cached tool results that used this audio SHA as any input.

        `inputs` maps an input name to its digest, and the name differs per
        tool - "file" for single-input tools, "beat"/"vocal" for Beat and
        Vocal Fit, "reference" for Reference Match. Querying one fixed name
        would silently miss every two-file tool, so the match is against the
        digest appearing as any value in the map.
        """
        if not self.enabled or not sha256:
            return []
        try:
            from pymongo import DESCENDING
            cur = (self.db().tool_results
                   .find({"input_digests": sha256}, {"result_gz": 0})
                   .sort("created_at", DESCENDING)
                   .limit(max(1, min(limit, 100))))
            return list(cur)
        except Exception:
            return []

    # -- tool results -------------------------------------------------------
    def get_tool_result(self, key: str) -> Optional[Dict]:
        """Cached tool output for an exact (bytes, tool, options) key."""
        if not self.enabled or not key:
            return None
        try:
            doc = self.db().tool_results.find_one({"_id": key})
            if not doc:
                return None
            payload = decompress_report({"report_gz": doc.get("result_gz")})
            if payload is None:
                return None
            self.db().tool_results.update_one(
                {"_id": key},
                {"$set": {"last_hit_at": _utcnow()}, "$inc": {"hits": 1}})
            return payload
        except Exception:
            log.debug("Tool cache read failed", exc_info=True)
            return None

    def save_tool_result(self, key: str, tool: str, digests: Dict,
                         params: Dict, result: Dict) -> bool:
        """Store a tool result, compressed, with the same TTL as an analysis."""
        if not self.enabled or not key:
            return False
        try:
            packed = compress_report(result)
            self.db().tool_results.replace_one(
                {"_id": key},
                {
                    "_id": key,
                    "tool": tool,
                    "inputs": digests,
                    # Flat array of the same digests, so "every result that
                    # touched this audio" is one index scan regardless of
                    # which input name the tool gave it.
                    "input_digests": sorted(set(digests.values())),
                    "params": params,
                    "result_gz": packed["report_gz"],
                    "result_bytes_gz": packed["report_bytes_gz"],
                    "hits": 0,
                    **({"expires_at": exp} if (exp := _expires_at(
                        self.cfg.result_ttl_days * 86400)) else {}),
                    "created_at": _utcnow(),
                },
                upsert=True)
            return True
        except Exception:
            log.debug("Tool cache write failed", exc_info=True)
            return False

    # -- idempotency --------------------------------------------------------
    def claim_idempotency(self, key: str, analysis_id: str) -> Optional[str]:
        """Reserve `key`. Returns the existing analysis id on collision.

        Uses an atomic upsert rather than catching DuplicateKeyError, so this
        stays correct even when the unique index is absent. That matters
        because ensure_indexes() is best-effort: if MongoDB is briefly
        unreachable at startup the index is never created, and an
        index-dependent implementation would silently stop deduplicating and
        run every retry as fresh work.

        ReturnDocument.BEFORE gives None when this call did the insert, and the
        pre-existing document when someone else already claimed the key.
        """
        if not self.enabled or not key:
            return None
        try:
            from pymongo import ReturnDocument
            prior = self.db().idempotency.find_one_and_update(
                {"key": key},
                {"$setOnInsert": {
                    "key": key,
                    "analysis_id": analysis_id,
                    "created_at": _utcnow(),
                    "expires_at": _utcnow() + timedelta(hours=24),
                }},
                upsert=True,
                return_document=ReturnDocument.BEFORE,
            )
            return prior.get("analysis_id") if prior else None
        except Exception:
            # Fail open: a broken idempotency store must not block analysis.
            log.warning("Idempotency check failed for key %s", key, exc_info=True)
            return None

    # -- usage --------------------------------------------------------------
    def record_usage(self, api_key_id: Optional[str], seconds: float,
                     mode: str) -> None:
        if not self.enabled or not api_key_id:
            return
        try:
            day = _utcnow().strftime("%Y-%m-%d")
            self.db().usage.update_one(
                {"api_key_id": api_key_id, "day": day},
                {"$inc": {"count": 1,
                          "compute_seconds": round(seconds, 2),
                          f"by_mode.{mode}": 1},
                 "$setOnInsert": {"created_at": _utcnow()}},
                upsert=True)
        except Exception:
            log.debug("Usage write failed", exc_info=True)

    def usage_today(self, api_key_id: str) -> int:
        if not self.enabled:
            return 0
        try:
            day = _utcnow().strftime("%Y-%m-%d")
            doc = self.db().usage.find_one({"api_key_id": api_key_id, "day": day})
            return int(doc.get("count", 0)) if doc else 0
        except Exception:
            return 0

    def usage_summary(self, api_key_id: str, days: int = 30) -> List[Dict]:
        if not self.enabled:
            return []
        try:
            from pymongo import DESCENDING
            cutoff = (_utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
            cur = (self.db().usage
                   .find({"api_key_id": api_key_id, "day": {"$gte": cutoff}},
                         {"_id": 0})
                   .sort("day", DESCENDING))
            return list(cur)
        except Exception:
            return []

    def health(self) -> Dict:
        if not self.enabled:
            return {"enabled": False}
        try:
            self.db().command("ping")
            return {"enabled": True, "ok": True,
                    "database": self.cfg.mongo_db,
                    "analyses": self.db().analyses.estimated_document_count()}
        except Exception as exc:
            return {"enabled": True, "ok": False, "error": str(exc)}


# --------------------------------------------------------------------------
# singletons
# --------------------------------------------------------------------------
_mongo: Optional[MongoStore] = None
_audio: Optional[AudioStore] = None
_slock = threading.Lock()


def get_mongo(cfg: Optional[StorageConfig] = None) -> MongoStore:
    global _mongo
    with _slock:
        if _mongo is None:
            from ..core.config import get_settings
            _mongo = MongoStore(cfg or get_settings().storage)
        return _mongo


def get_audio_store(cfg: Optional[StorageConfig] = None) -> AudioStore:
    global _audio
    with _slock:
        if _audio is None:
            from ..core.config import get_settings
            _audio = AudioStore(cfg or get_settings().storage)
        return _audio


def reset() -> None:
    """Test hook."""
    global _mongo, _audio
    with _slock:
        _mongo = _audio = None
