"""
Object storage for uploads and derived caches.

Two backends behind one interface:

- **LocalStorage** - a directory. What the desktop app uses, and what the
  tests run against. "Signed URLs" are just paths back into this app, so the
  hosted upload flow can be exercised end to end without a cloud account.

- **GcsStorage** - Google Cloud Storage. The reason this abstraction exists at
  all is Cloud Run's 32 MB request body limit: a 160 MB study result cannot be
  POSTed to the app, so the browser has to PUT it straight to the bucket using
  a signed URL and then tell the app the object is there. The file never
  passes through the service.

Keys are POSIX-style paths ("uploads/<session>/<name>"), never absolute paths
from a caller - see _safe_key.
"""
import os
import re
import shutil

_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,255}$")


def _safe_key(key: str) -> str:
    """Keys come from request data, so they get the same suspicion as any
    other path input: no absolute paths, no traversal, no surprises."""
    if not isinstance(key, str) or not _KEY_RE.match(key):
        raise ValueError(f"Invalid storage key: {key!r}")
    if ".." in key.split("/"):
        raise ValueError(f"Invalid storage key: {key!r}")
    return key


class LocalStorage:
    """Files under a root directory."""

    kind = "local"

    def __init__(self, root: str):
        self.root = os.path.abspath(root)
        os.makedirs(self.root, exist_ok=True)

    def _path(self, key: str) -> str:
        p = os.path.join(self.root, _safe_key(key).replace("/", os.sep))
        # Belt and braces: even with a validated key, confirm we stayed inside.
        if not os.path.abspath(p).startswith(self.root + os.sep):
            raise ValueError(f"Key escapes storage root: {key!r}")
        return p

    def exists(self, key):
        return os.path.isfile(self._path(key))

    def size(self, key):
        return os.path.getsize(self._path(key))

    def upload_from(self, local_path, key):
        dest = self._path(key)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copyfile(local_path, dest)
        return key

    def download_to(self, key, local_path):
        os.makedirs(os.path.dirname(os.path.abspath(local_path)), exist_ok=True)
        shutil.copyfile(self._path(key), local_path)
        return local_path

    def delete(self, key):
        try:
            os.remove(self._path(key))
            return True
        except FileNotFoundError:
            return False

    def signed_upload_url(self, key, content_type=None, max_bytes=None, expires=900):
        """No signing to do - the caller PUTs back to this app, which writes
        the bytes through /api/upload/direct."""
        return {"url": f"/api/upload/direct?key={_safe_key(key)}", "method": "PUT",
                "headers": {}, "backend": "local"}


class GcsStorage:
    """Google Cloud Storage. Import is lazy so the desktop app never needs the
    dependency."""

    kind = "gcs"

    def __init__(self, bucket_name: str):
        from google.cloud import storage as gcs  # noqa: PLC0415

        self._client = gcs.Client()
        self._bucket = self._client.bucket(bucket_name)
        self.bucket_name = bucket_name

    def _blob(self, key):
        return self._bucket.blob(_safe_key(key))

    def exists(self, key):
        return self._blob(key).exists()

    def size(self, key):
        b = self._blob(key)
        b.reload()
        return b.size

    def upload_from(self, local_path, key):
        self._blob(key).upload_from_filename(local_path)
        return key

    def download_to(self, key, local_path):
        os.makedirs(os.path.dirname(os.path.abspath(local_path)), exist_ok=True)
        self._blob(key).download_to_filename(local_path)
        return local_path

    def delete(self, key):
        try:
            self._blob(key).delete()
            return True
        except Exception:
            return False

    def signed_upload_url(self, key, content_type=None, max_bytes=None, expires=900):
        """A V4 signed PUT the browser uses directly.

        x-goog-content-length-range makes GCS itself reject anything outside
        the size band, so an oversized upload is refused at the bucket and
        never becomes our problem or our bill."""
        import datetime  # noqa: PLC0415

        headers = {}
        if max_bytes:
            headers["x-goog-content-length-range"] = f"0,{int(max_bytes)}"
        if content_type:
            headers["Content-Type"] = content_type

        url = self._blob(key).generate_signed_url(
            version="v4",
            expiration=datetime.timedelta(seconds=expires),
            method="PUT",
            content_type=content_type,
            headers=headers,
        )
        return {"url": url, "method": "PUT", "headers": headers, "backend": "gcs"}


def build(bucket_name: str = "", local_root: str = ""):
    """GCS when a bucket is configured, otherwise a local directory."""
    if bucket_name:
        return GcsStorage(bucket_name)
    return LocalStorage(local_root)
