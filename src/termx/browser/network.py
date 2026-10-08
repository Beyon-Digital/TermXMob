"""Host-owned browser targets and a DNS-pinning HTTP CONNECT egress proxy.

Chromium gets no direct network socket or DNS resolution for HTTP destinations.
Each tunnel connects to the already validated address, preventing DNS rebinding.
Private development origins must be approved by host administrator before profiles
are created. Broker policy is not an OS sandbox; unrestricted shell remains trusted.
"""
from __future__ import annotations
import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit

class TargetDenied(ValueError): pass


def origin(url: str) -> str:
    p=urlsplit(url)
    if p.scheme not in {'http','https'} or not p.hostname or p.username or p.password or p.fragment:
        raise TargetDenied('only HTTP(S) destinations without credentials or fragments are allowed')
    port=p.port or (443 if p.scheme=='https' else 80)
    host=f'[{p.hostname}]' if ':' in p.hostname else p.hostname.lower().rstrip('.')
    return f'{p.scheme}://{host}' + (f':{port}' if port != (443 if p.scheme=='https' else 80) else '')

class NetworkPolicy:
    def __init__(self, development_origins=()):
        self.development_origins=frozenset(origin(o) for o in development_origins)
    async def resolve(self, host: str, port: int, scheme: str):
        if host.lower().rstrip('.') in {'metadata.google.internal','metadata','instance-data'}:
            raise TargetDenied('cloud metadata is denied')
        target=origin(f'{scheme}://'+(f'[{host}]' if ':' in host else host)+f':{port}')
        rows=await asyncio.get_running_loop().getaddrinfo(host,port,type=socket.SOCK_STREAM)
        addresses=list(dict.fromkeys(r[4][0] for r in rows))
        if not addresses: raise TargetDenied('destination has no address')
        for address in addresses:
            ip=ipaddress.ip_address(address)
            if ip.is_link_local or ip.is_multicast or ip.is_unspecified or ip.is_reserved:
                raise TargetDenied('metadata and reserved destinations are denied')
            if not ip.is_global and target not in self.development_origins:
                raise TargetDenied('private destination requires an exact host development grant')
        return addresses[0]
    async def validate(self,url):
        destination=origin(url)
        p=urlsplit(url)
        await self.resolve(p.hostname,p.port or (443 if p.scheme=='https' else 80),p.scheme)
        return destination

class EgressProxy:
    def __init__(self,policy: NetworkPolicy):
        self.policy=policy
        self.server=None
        self.tasks=set()
    async def start(self):
        self.server=await asyncio.start_server(self._client,'127.0.0.1',0,limit=65536)
        return f'http://127.0.0.1:{self.server.sockets[0].getsockname()[1]}'
    async def close(self):
        if self.server:
            self.server.close(); await self.server.wait_closed()
        for task in list(self.tasks): task.cancel()
        await asyncio.gather(*self.tasks,return_exceptions=True)
    async def _client(self,reader,writer):
        task=asyncio.current_task();self.tasks.add(task)
        upstream=None
        try:
            header=await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'),10)
            method,target,version=header.split(b'\r\n',1)[0].decode('ascii').split(' ')
            if method=='CONNECT':
                p=urlsplit('https://'+target)
                host,port=p.hostname,p.port or 443
                address=await self.policy.resolve(host,port,'https')
                remote,upstream=await asyncio.wait_for(asyncio.open_connection(address,port),15)
                writer.write(b'HTTP/1.1 200 Connection Established\r\n\r\n');await writer.drain()
            else:
                p=urlsplit(target);origin(target)
                if p.scheme!='http': raise TargetDenied('invalid proxy request')
                host,port=p.hostname,p.port or 80
                address=await self.policy.resolve(host,port,'http')
                remote,upstream=await asyncio.wait_for(asyncio.open_connection(address,port),15)
                path=(p.path or '/')+('?' + p.query if p.query else '')
                lines=header.split(b'\r\n')
                clean=[line for line in lines[1:] if not line.lower().startswith((b'proxy-',b'connection:'))]
                upstream.write(f'{method} {path} {version}\r\n'.encode()+b'\r\n'.join(clean));await upstream.drain()
            async def copy(source,destination):
                transferred=0
                while chunk:=await source.read(65536):
                    transferred+=len(chunk)
                    if transferred>32*1024*1024:
                        raise TargetDenied('managed browser connection transfer limit exceeded')
                    destination.write(chunk);await destination.drain()
            copies=[asyncio.create_task(copy(reader,upstream)),asyncio.create_task(copy(remote,writer))]
            try:
                await asyncio.wait(copies,return_when=asyncio.FIRST_COMPLETED)
            finally:
                for copy_task in copies:copy_task.cancel()
                await asyncio.gather(*copies,return_exceptions=True)
        except (Exception,asyncio.CancelledError):
            if not writer.is_closing():
                writer.write(b'HTTP/1.1 403 Forbidden\r\nContent-Length:0\r\nConnection:close\r\n\r\n')
        finally:
            if upstream: upstream.close()
            writer.close(); self.tasks.discard(task)
