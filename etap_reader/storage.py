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
        # Separate, cloud-platform-scoped credentials used only for signing.
        self._signing_creds = None
        self._signing_email = None

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

    @staticmethod
    def _metadata_email():
        """The metadata server answers "default" to the credentials object but
        will give the real address if asked directly."""
        import urllib.request  # noqa: PLC0415

        req = urllib.request.Request(
            "http://metadata.google.internal/computeMetadata/v1/instance/"
            "service-accounts/default/email",
            headers={"Metadata-Flavor": "Google"})
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.read().decode()

    def _signing_kwargs(self):
        """Signing a URL needs a private key, and on Cloud Run there isn't one.

        The ambient credentials there are compute-engine credentials with no
        key material, so signing goes through the IAM signBlob API instead -
        which the library will do, but only if handed the service account's
        address and an access token.

        The token has to be minted separately. storage.Client() authenticates
        with storage-only scopes, and reusing that token for the IAM API fails
        with ACCESS_TOKEN_SCOPE_INSUFFICIENT - a 403 that reads like a missing
        role and is in fact a missing scope. signBlob needs cloud-platform.

        Also requires iamcredentials.googleapis.com enabled and the service
        account holding roles/iam.serviceAccountTokenCreator on itself.
        """
        # Service-account-key credentials expose .signer and can sign locally;
        # compute-engine ones cannot, and that is the distinction here.
        if hasattr(self._client._credentials, "signer"):
            return {}

        from google.auth import default as google_default  # noqa: PLC0415
        from google.auth.transport import requests as google_requests  # noqa: PLC0415

        if self._signing_creds is None:
            self._signing_creds, _ = google_default(
                scopes=["https://www.googleapis.com/auth/cloud-platform"])
        # Tokens last about an hour; refresh only when one has actually gone
        # stale rather than on every upload.
        if not self._signing_creds.valid:
            self._signing_creds.refresh(google_requests.Request())

        email = getattr(self._signing_creds, "service_account_email", None)
        if not email or email == "default":
            email = self._signing_email or self._metadata_email()
        self._signing_email = email
        return {"service_account_email": email,
                "access_token": self._signing_creds.token}

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
            **self._signing_kwargs(),
        )
        return {"url": url, "method": "PUT", "headers": headers, "backend": "gcs"}


def build(bucket_name: str = "", local_root: str = ""):
    """GCS when a bucket is configured, otherwise a local directory."""
    if bucket_name:
        return GcsStorage(bucket_name)
    return LocalStorage(local_root)
