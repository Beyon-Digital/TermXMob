"""Bounded HTTPS registry retrieval with pinned public DNS and verified TLS.

The optional resolver/context are trusted host composition hooks, never request
fields. Credentials travel only to the exact configured origin; no proxy or
cross-origin redirect is used. Registry packages remain inert JSON text bundles.
"""
from __future__ import annotations

import hashlib
import http.client
import ipaddress
import json
import queue
import threading
import socket
import ssl
import time
from urllib.parse import urljoin, urlsplit

MAX_BYTES = 2_100_000
MAX_SECONDS = 12
MAX_DNS_SECONDS = 3
_DNS_SLOTS = threading.BoundedSemaphore(4)


def checked_url(value: str):
    if not isinstance(value, str) or len(value)>2048 or any(ord(c)<33 for c in value):
        raise ValueError('Registry URL is invalid')
    parsed=urlsplit(value)
    if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise ValueError('Registry requires an HTTPS URL without embedded credentials or fragment')
    if parsed.query:
        raise ValueError('Registry URLs must not include query strings; use the host credential reference')
    port=parsed.port or 443
    return parsed, (parsed.hostname.lower(),port)


def public_addresses(host,port):
    # getaddrinfo has no portable per-call timeout. Bound admission and caller
    # waiting without an executor whose shutdown could block on a stuck resolver.
    slots=_DNS_SLOTS
    if not slots.acquire(blocking=False):
        raise ValueError('Registry DNS lookup capacity is busy')
    result=queue.Queue(maxsize=1)
    def resolve():
        try:
            result.put((True,socket.getaddrinfo(host,port,type=socket.SOCK_STREAM)))
        except Exception as exc:
            result.put((False,exc))
        finally:
            slots.release()
    try:
        threading.Thread(target=resolve,name='termx-registry-dns',daemon=True).start()
    except BaseException:
        slots.release()
        raise
    try:
        succeeded,answers=result.get(timeout=MAX_DNS_SECONDS)
    except queue.Empty:
        raise ValueError('Registry DNS lookup exceeded its time budget') from None
    if not succeeded:
        raise ValueError('Registry DNS lookup failed') from None
    addresses=sorted({answer[4][0] for answer in answers})
    if not addresses or len(addresses)>16:
        raise ValueError('Registry DNS did not return a bounded address set')
    for address in addresses:
        ip=ipaddress.ip_address(address)
        if getattr(ip,'ipv4_mapped',None):
            ip=ip.ipv4_mapped
        if not ip.is_global:
            raise ValueError('Registry addresses must be public; protected networks are refused')
    return addresses


class PinnedConnection(http.client.HTTPSConnection):
    def __init__(self,host,port,address,context,timeout):
        super().__init__(host,port,timeout=timeout,context=context)
        self.address=address

    def connect(self):
        sock=socket.create_connection((self.address,self.port),self.timeout)
        try:
            self.sock=self._context.wrap_socket(sock,server_hostname=self.host)
        except BaseException:
            sock.close()
            raise


class HTTPSRegistryTransport:
    def __init__(self,*,resolver=public_addresses,context=None):
        self.resolver=resolver
        self.context=context or ssl.create_default_context()
        if self.context.verify_mode!=ssl.CERT_REQUIRED or not self.context.check_hostname:
            raise ValueError('Registry TLS verification cannot be disabled')

    def fetch(self,url,*,origin_url=None,credential=None):
        _,origin=checked_url(origin_url or url)
        started=time.monotonic()
        for _ in range(4):
            parsed,current=checked_url(url)
            if current!=origin:
                raise ValueError('Registry redirects and packages must stay on the configured origin')
            addresses=self.resolver(*current)
            if not addresses or len(addresses)>16:
                raise ValueError('Registry address set is invalid')
            timeout=MAX_SECONDS-(time.monotonic()-started)
            if timeout<=0:
                raise ValueError('Registry download exceeded its time budget')
            connection=PinnedConnection(*current,addresses[0],self.context,timeout)
            headers={'Accept':'application/json','Accept-Encoding':'identity','User-Agent':'TermX-Registry/1'}
            if credential:
                if len(credential)>4096 or any(ord(c)<32 or ord(c)>126 for c in credential):
                    raise ValueError('Registry credential is invalid')
                headers['Authorization']='Bearer '+credential
            try:
                connection.request('GET',(parsed.path or '/')+('?' + parsed.query if parsed.query else ''),headers=headers)
                response=connection.getresponse()
                if response.status in {301,302,303,307,308}:
                    location=response.getheader('Location')
                    if not location:
                        raise ValueError('Registry redirect has no destination')
                    url=urljoin(url,location)
                    if checked_url(url)[1]!=origin:
                        raise ValueError('Registry redirect changed origin; credentials were not forwarded')
                    continue
                if response.status!=200:
                    raise ValueError(f'Registry returned HTTP {response.status}')
                if response.getheader('Content-Encoding','identity').lower()!='identity':
                    raise ValueError('Registry compressed payloads are refused')
                size=response.getheader('Content-Length')
                if size is not None and (not size.isdecimal() or int(size)>MAX_BYTES):
                    raise ValueError('Registry payload exceeds its byte budget')
                chunks=[];received=0
                while True:
                    remaining=MAX_SECONDS-(time.monotonic()-started)
                    if remaining<=0:
                        raise ValueError('Registry download exceeded its time budget')
                    if connection.sock:
                        connection.sock.settimeout(remaining)
                    block=response.read(min(64_000,MAX_BYTES+1-received))
                    if not block:
                        break
                    chunks.append(block);received+=len(block)
                    if received>MAX_BYTES:
                        raise ValueError('Registry payload exceeds its byte budget')
                raw=b''.join(chunks)
                try:
                    result=json.loads(raw.decode('utf-8'))
                except (UnicodeError,ValueError,RecursionError):
                    raise ValueError('Registry must return UTF-8 JSON') from None
                if not isinstance(result,dict):
                    raise ValueError('Registry payload must be a JSON object')
                return result, {'url':url,'sha256':hashlib.sha256(raw).hexdigest(),'bytes':received}
            except (http.client.HTTPException,OSError) as exc:
                raise ValueError('Registry HTTPS transport failed: '+type(exc).__name__) from None
            finally:
                connection.close()
        raise ValueError('Registry redirect limit exceeded')
