"""A minimal 'remote' for sharing impact maps across CI runners.

Local maps under ``.tia/`` are per-checkout. In CI the runner that builds
the map (on the base branch) is almost never the runner that consumes it
(on a PR), so the map has to live somewhere shared.

Maps are addressed by the **git ref they were recorded at**, so a PR job
can pull the exact map built for its base. A ``latest.json`` pointer is
also kept as a fallback when the consumer doesn't know the precise ref.

Backends, picked from the remote string's scheme:

* ``http://`` / ``https://`` — talk to ``tia.server`` (or any store that
  answers GET/PUT on ``/maps/<name>``). Zero-friction, zero-dependency.
* ``s3://bucket/prefix`` — an S3 (or S3-compatible) bucket. Needs ``boto3``.
* ``gs://bucket/prefix`` — Google Cloud Storage. Needs ``google-cloud-storage``.
* anything else — a plain directory (a mounted cache volume, an artifact
  dir synced to/from object storage, a checked-out cache repo).

The cloud SDKs are imported lazily, *only* when their scheme is used, so
tia keeps its zero-dependency install for everyone who doesn't reach for
them. Every backend speaks the same tiny ``put``/``get`` by object name,
so ``push``/``pull`` don't care which one they're driving.
"""

import os
import shutil
import urllib.error
import urllib.request
from urllib.parse import urlparse

LATEST = "latest.json"


def _key(ref: str | None) -> str:
    """Safe object name for a ref. None/unknown collapses to latest."""
    if not ref:
        return LATEST
    safe = "".join(c if c.isalnum() or c in "-._" else "_" for c in ref)
    return f"{safe}.json"


def _write(dest: str, data: bytes) -> None:
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    with open(dest, "wb") as fh:
        fh.write(data)


# --- backends -------------------------------------------------------------
# Each backend stores objects under a ``maps/<name>`` namespace and answers
# ``get`` with the bytes (or None if the object is absent).


class _DirBackend:
    """A plain directory. Keeps the flat ``<remote>/<name>`` layout."""

    def __init__(self, remote: str) -> None:
        self.root = remote

    def put(self, name: str, data: bytes) -> None:
        os.makedirs(self.root, exist_ok=True)
        with open(os.path.join(self.root, name), "wb") as fh:
            fh.write(data)

    def get(self, name: str) -> bytes | None:
        src = os.path.join(self.root, name)
        if not os.path.exists(src):
            return None
        with open(src, "rb") as fh:
            return fh.read()

    def locator(self, name: str) -> str:
        return os.path.join(self.root, name)


class _HttpBackend:
    def __init__(self, remote: str) -> None:
        self.base = remote.rstrip("/")

    def _url(self, name: str) -> str:
        return f"{self.base}/maps/{name}"

    def put(self, name: str, data: bytes) -> None:
        req = urllib.request.Request(self._url(name), data=data, method="PUT")
        with urllib.request.urlopen(req, timeout=30):
            pass

    def get(self, name: str) -> bytes | None:
        try:
            with urllib.request.urlopen(self._url(name), timeout=30) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            raise

    def locator(self, name: str) -> str:
        return self._url(name)


def _s3_client():  # pragma: no cover - thin SDK wrapper, faked in tests
    try:
        import boto3
    except ImportError as e:
        raise RuntimeError(
            "s3:// remotes need boto3 — `pip install boto3` (or `pytest-tia[s3]`)."
        ) from e
    return boto3.client("s3")


class _S3Backend:
    def __init__(self, remote: str) -> None:
        u = urlparse(remote)
        self.bucket = u.netloc
        self.prefix = u.path.strip("/")
        self._c = None

    @property
    def client(self):
        if self._c is None:
            self._c = _s3_client()
        return self._c

    def _key(self, name: str) -> str:
        parts = [self.prefix, "maps", name] if self.prefix else ["maps", name]
        return "/".join(parts)

    def put(self, name: str, data: bytes) -> None:
        self.client.put_object(Bucket=self.bucket, Key=self._key(name), Body=data)

    def get(self, name: str) -> bytes | None:
        try:
            obj = self.client.get_object(Bucket=self.bucket, Key=self._key(name))
        except self.client.exceptions.NoSuchKey:
            return None
        return obj["Body"].read()

    def locator(self, name: str) -> str:
        return f"s3://{self.bucket}/{self._key(name)}"


def _gcs_client():  # pragma: no cover - thin SDK wrapper, faked in tests
    try:
        from google.cloud import storage
    except ImportError as e:
        raise RuntimeError(
            "gs:// remotes need google-cloud-storage — "
            "`pip install google-cloud-storage` (or `pytest-tia[gcs]`)."
        ) from e
    return storage.Client()


def _gcs_not_found():  # pragma: no cover - resolved lazily, faked in tests
    from google.cloud import exceptions
    return exceptions.NotFound


class _GcsBackend:
    def __init__(self, remote: str) -> None:
        u = urlparse(remote)
        self.bucket_name = u.netloc
        self.prefix = u.path.strip("/")
        self._c = None

    @property
    def client(self):
        if self._c is None:
            self._c = _gcs_client()
        return self._c

    def _key(self, name: str) -> str:
        parts = [self.prefix, "maps", name] if self.prefix else ["maps", name]
        return "/".join(parts)

    def _blob(self, name: str):
        return self.client.bucket(self.bucket_name).blob(self._key(name))

    def put(self, name: str, data: bytes) -> None:
        self._blob(name).upload_from_string(data)

    def get(self, name: str) -> bytes | None:
        try:
            return self._blob(name).download_as_bytes()
        except _gcs_not_found():
            return None

    def locator(self, name: str) -> str:
        return f"gs://{self.bucket_name}/{self._key(name)}"


def _backend(remote: str):
    if remote.startswith(("http://", "https://")):
        return _HttpBackend(remote)
    if remote.startswith("s3://"):
        return _S3Backend(remote)
    if remote.startswith("gs://"):
        return _GcsBackend(remote)
    return _DirBackend(remote)


# --- public API -----------------------------------------------------------

def push(local_map_path: str, remote: str, ref: str | None) -> str:
    """Publish the local map under its ref, and update the latest pointer."""
    with open(local_map_path, "rb") as fh:
        data = fh.read()
    backend = _backend(remote)
    key = _key(ref)
    backend.put(key, data)
    backend.put(LATEST, data)
    return backend.locator(key)


def pull(remote: str, ref: str | None, dest: str) -> str | None:
    """Fetch the map for ``ref`` (else latest) into ``dest``. None if absent."""
    backend = _backend(remote)
    for name in ([_key(ref), LATEST] if ref else [LATEST]):
        data = backend.get(name)
        if data is not None:
            _write(dest, data)
            return backend.locator(name)
    return None
