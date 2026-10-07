"""Private credential binding. Reports expose only an opaque revision, never a key hash."""
import hashlib
import hmac
import json
import secrets
import threading

_LOCK = threading.RLock()


def account_revision(records, provider, credential):
    with _LOCK:
        return _account_revision(records, provider, credential)


def _account_revision(records, provider, credential):
    row = records.get('reviewer-private-binding', provider['id'])
    salt = row['salt'] if row else secrets.token_hex(32)
    material = json.dumps({'provider_id': provider['id'], 'kind': provider['kind'],
                           'base_url': provider['base_url'], 'model': provider['model'],
                           'credential': credential}, sort_keys=True).encode()
    fingerprint = hmac.new(bytes.fromhex(salt), material, hashlib.sha256).hexdigest()
    if not row or not hmac.compare_digest(row['fingerprint'], fingerprint):
        row = {'id': provider['id'], 'salt': salt, 'fingerprint': fingerprint, 'revision': secrets.token_urlsafe(24)}
        records.put('reviewer-private-binding', provider['id'], row)
    return row['revision']
