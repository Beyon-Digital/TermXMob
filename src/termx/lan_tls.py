"""Opt-in LAN TLS, independent of the desktop's private loopback broker.

The CA is an installation identity, never a discovery credential. Nothing here
installs OS trust, changes firewall rules, or disables certificate validation.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import base64
import hashlib
import ipaddress
import json
import re
import ssl
from pathlib import Path
from urllib.parse import urlsplit

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from termx.config import atomic_write
from termx.host_instance import private_owned
from termx.net import lan_ipv4_addresses

UTC = timezone.utc
PRIVATE_NETWORKS = tuple(ipaddress.ip_network(value) for value in (
    '127.0.0.0/8', '10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16',
    '::1/128', 'fc00::/7', 'fe80::/10',
))


def supported_name(value: str) -> str:
    value = value.strip().lower().rstrip('.')
    if '%' in value:
        raise ValueError('Scoped IPv6 addresses are not supported; use a .local name or stable private address')
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        if value != 'localhost' and not (value.endswith('.local') and re.fullmatch(r'(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+local', value)):
            raise ValueError('LAN TLS names must be localhost, a .local name, or a private IP address')
    else:
        if not any(address.version == network.version and address in network for network in PRIVATE_NETWORKS):
            raise ValueError('LAN TLS requires a loopback or private LAN address')
        value = str(address)
    return value


def advertised_names(bind: str, extra: list[str]) -> list[str]:
    address = ipaddress.ip_address(bind)
    wildcard = bind in {'0.0.0.0', '::'}
    names = ['localhost', '127.0.0.1'] if bind == '0.0.0.0' else ['localhost', '::1'] if bind == '::' else [supported_name(bind)]
    if bind == '0.0.0.0':
        names.extend(lan_ipv4_addresses())
    for name in extra:
        value = supported_name(name)
        try:
            candidate = ipaddress.ip_address(value)
        except ValueError:
            names.append(value)
            continue
        if candidate.version != address.version or (not wildcard and candidate != address):
            raise ValueError('Additional IP SANs must be reachable on the selected listener interface and address family')
        names.append(value)
    return sorted(set(names))



def _san(name: str):
    try:
        return x509.IPAddress(ipaddress.ip_address(name))
    except ValueError:
        return x509.DNSName(name)


def _pem_key(key) -> str:
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption()).decode('ascii')


def _pem_cert(cert) -> str:
    return cert.public_bytes(serialization.Encoding.PEM).decode('ascii')


def spki(cert) -> str:
    key = cert.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    return base64.b64encode(hashlib.sha256(key).digest()).decode('ascii')


@dataclass
class LanIdentity:
    root: Path
    ca: x509.Certificate
    ca_key: ec.EllipticCurvePrivateKey
    key: ec.EllipticCurvePrivateKey
    certificate: x509.Certificate
    names: list[str]

    @property
    def bundle(self) -> Path:
        return self.root / 'server.pem'

    def metadata(self, port: int) -> dict:
        urls = []
        for name in self.names:
            address = f'[{name}]' if ':' in name else name
            urls.append(f'https://{address}:{port}')
        return {'enabled': True, 'urls': urls, 'names': self.names,
                'ca_fingerprint': self.ca.fingerprint(hashes.SHA256()).hex(),
                'public_key_hashes': [spki(self.ca), spki(self.certificate)],
                'ca_der': base64.b64encode(self.ca.public_bytes(serialization.Encoding.DER)).decode('ascii'),
                'expires_at': self.certificate.not_valid_after_utc.isoformat(),
                'ca_expires_at': self.ca.not_valid_after_utc.isoformat(),
                'trust': 'Install the public CA only after verifying its fingerprint on this host. Native clients also require QR key pairing.'}

    def renew(self, names: list[str], *, now: datetime | None = None) -> bool:
        now = now or datetime.now(UTC)
        if self.ca.not_valid_after_utc <= now + timedelta(days=31):
            raise RuntimeError('LAN TLS identity expires soon. Stop the host and deliberately rotate it, then trust and pair each client again.')
        if names == self.names and self.certificate.not_valid_after_utc > now + timedelta(days=7):
            return False
        certificate = _leaf(self.ca, self.ca_key, self.key, names, now)
        atomic_write(self.bundle, _pem_key(self.key) + _pem_cert(certificate) + _pem_cert(self.ca))
        self.certificate, self.names = certificate, names
        return True


def _leaf(ca, ca_key, key, names, now):
    return (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'TermX LAN')]))
            .issuer_name(ca.subject).public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=5)).not_valid_after(now + timedelta(days=30))
            .add_extension(x509.SubjectAlternativeName([_san(name) for name in names]), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.KeyUsage(True, False, False, False, False, False, False, False, False), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
            .sign(ca_key, hashes.SHA256()))


def load_identity(profile: Path, names: list[str], *, rotate=False, now=None) -> LanIdentity:
    now = now or datetime.now(UTC)
    names = sorted({supported_name(value) for value in names})
    profile.mkdir(parents=True, exist_ok=True, mode=0o700)
    private_owned(profile, directory=True)
    root = profile / 'lan-tls'
    if root.is_symlink():
        raise PermissionError('LAN TLS storage must not be a symbolic link')
    root.mkdir(mode=0o700, exist_ok=True)
    private_owned(root, directory=True)
    path, bundle = root / 'identity.json', root / 'server.pem'
    for candidate in (path, bundle, root / 'ca.pem'):
        if candidate.exists() or candidate.is_symlink():
            private_owned(candidate)
    if path.exists() and not rotate:
        try:
            value = json.loads(path.read_text())
            ca = x509.load_pem_x509_certificate(value['ca'].encode())
            ca_key = serialization.load_pem_private_key(value['ca_key'].encode(), None)
            key = serialization.load_pem_private_key(value['server_key'].encode(), None)
            if not isinstance(ca_key, ec.EllipticCurvePrivateKey) or not isinstance(key, ec.EllipticCurvePrivateKey):
                raise ValueError('Unsupported identity key')
            if ca.public_key().public_numbers() != ca_key.public_key().public_numbers():
                raise ValueError('CA key mismatch')
            ca.verify_directly_issued_by(ca)
            if ca.not_valid_before_utc > now or ca.not_valid_after_utc <= now + timedelta(days=31):
                raise ValueError('CA expired or clock incorrect')
        except Exception as exc:
            raise RuntimeError('LAN TLS identity is invalid. Restore its private backup or deliberately rotate and re-pair; identity will not reset automatically.') from exc
    else:
        # An interrupted initial write must not silently overwrite an existing identity.
        if not rotate and bundle.exists():
            raise RuntimeError('LAN TLS identity metadata is missing. Restore a backup or deliberately rotate and re-pair.')
        ca_key, key = ec.generate_private_key(ec.SECP256R1()), ec.generate_private_key(ec.SECP256R1())
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'TermX private LAN CA ' + hashlib.sha256(ca_key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)).hexdigest()[:12])])
        ca = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(ca_key.public_key())
              .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=5))
              .not_valid_after(now + timedelta(days=3650)).add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
              .add_extension(x509.KeyUsage(True, False, False, False, False, True, True, False, False), critical=True)
              .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
              .add_extension(x509.NameConstraints([x509.DNSName('localhost'), x509.DNSName('.local'), *[x509.IPAddress(network) for network in PRIVATE_NETWORKS]], None), critical=True)
              .sign(ca_key, hashes.SHA256()))
        atomic_write(path, json.dumps({'schema': 1, 'ca': _pem_cert(ca), 'ca_key': _pem_key(ca_key), 'server_key': _pem_key(key)}))
    certificate = None
    if bundle.exists() and not rotate:
        try:
            data = bundle.read_bytes()
            certificate = x509.load_pem_x509_certificate(data)
            stored_key = serialization.load_pem_private_key(data, None)
            certificate.verify_directly_issued_by(ca)
            if certificate.public_key().public_numbers() != key.public_key().public_numbers() or stored_key.public_key().public_numbers() != key.public_key().public_numbers():
                raise ValueError('Server key mismatch')
            old_names = sorted(str(item.value) for item in certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName).value)
        except Exception as exc:
            raise RuntimeError('LAN TLS server identity is invalid; restore its backup or deliberately rotate.') from exc
    else:
        old_names = names
        certificate = _leaf(ca, ca_key, key, names, now)
        atomic_write(bundle, _pem_key(key) + _pem_cert(certificate) + _pem_cert(ca))
    atomic_write(root / 'ca.pem', _pem_cert(ca))
    identity = LanIdentity(root, ca, ca_key, key, certificate, old_names)
    identity.renew(names, now=now)
    return identity


def ssl_context(identity: LanIdentity) -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(identity.bundle)
    return context


def binding_for(state, address: str | None = None) -> dict | None:
    identity = getattr(state, 'lan_identity', None)
    if identity is None:
        return None
    metadata = identity.metadata(state.lan_port)
    if address and address.rstrip('/') not in metadata['urls']:
        return None
    return {key: metadata[key] for key in ('urls', 'public_key_hashes', 'ca_fingerprint', 'ca_der')}


def connection_urls(state) -> list[str]:
    identity = getattr(state, 'lan_identity', None)
    if identity is None:
        from termx.net import http_urls
        return http_urls(state.port)
    # Prefer reachable LAN candidates over the certificate's loopback SANs.
    urls = identity.metadata(state.lan_port)['urls']
    return sorted(urls, key=lambda url: urlsplit(url).hostname in {'localhost', '127.0.0.1', '::1'})
