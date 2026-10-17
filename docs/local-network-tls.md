# Local-network HTTPS and WSS

TermX has two listeners. The desktop broker binds to loopback (`127.0.0.1:8787` by default) and keeps the existing Tauri IPC/session boundary. An **opt-in** second Uvicorn listener serves the same application over TLS. No domain, reverse proxy, Docker, firewall modification or automatic root installation is required.

## Setup

1. Set up the host owner locally and sign in.
2. Open the drawer's Settings → Access & sessions → Local network HTTPS. Choose a port (default `8788`), optionally a specific private bind address, and additional resolving `.local` names. Save and restart the host deliberately. Saving configuration does not open a socket or install trust.
3. Alternatively start the host with:

   ```sh
   termx --host 127.0.0.1 --lan-tls --lan-port 8788 --lan-name laptop.local
   ```

4. Use an advertised **HTTPS** address. Native pairing uses the one-use QR under Settings → Access & sessions. Confirm its CA fingerprint on the phone against the host's screen before accepting it.

The CLI and Tauri broker now default to loopback. An old `--host 0.0.0.0` invocation fails with instructions to use `--lan-tls`; it never silently creates a plaintext LAN service. Existing background services should use their loopback configuration. The LAN setting is shared by desktop and service processes using the same private profile.

`.local` SANs support names already resolved by the network's mDNS responder. This change does **not** register an mDNS name or service. Address discovery is a hint, never authentication. IPv4 wildcard listeners refresh detected private IPv4 SANs every 60 seconds. Supply additional addresses explicitly when the default route is not the desired interface. The listener advertises only its configured address family. IPv6 uses `--lan-host ::` plus explicit private IPv6 SANs; scoped link-local addresses such as `fe80::1%en0` are unsupported. A specific-IP listener accepts only that IP as an additional IP SAN. Public IPs, public DNS names, bare hostnames and wildcards are rejected by this local-CA mode; public tunnels retain their own certificate path.

## Browser trust

A browser cannot authenticate a private CA by JavaScript fingerprint pinning. It needs the installation's public CA trusted through its browser/OS controls:

- Transfer **only** `lan-tls/ca.pem` from the host's private TermX profile using an authenticated channel, such as an already trusted file transfer. Settings shows its SHA-256 certificate fingerprint.
- Compare that fingerprint on the host before importing it. A certificate downloaded through an untrusted warning is not proof of identity.
- Use the platform's normal certificate-import UI: Keychain Access on macOS; the current user's certificate manager on Windows; the browser's authorities manager where it maintains its own store. On iOS, installing a profile and enabling certificate trust are separate deliberate platform actions. Managed devices may prohibit this.
- Trusting this CA grants certificate authority within its constrained LAN namespace (`.local`, localhost and private IP ranges), not just application login. Only trust a host whose owner can protect that key. Native clients instead use an app-private anchor and need no OS root installation.

TermX never installs a root. Do not bypass a warning or switch to HTTP. Browser WSS uses the same verified certificate as HTTPS. Existing browser cookies, CSRF, scopes, session revocation and one-use socket tickets remain required.

## Identity, storage and lifecycle

Each private profile has its own EC P-256 CA and server key. `lan-tls/identity.json` and `lan-tls/server.pem` contain private material; directory mode is `0700`, files `0600` on Unix, and the existing protected owner/SYSTEM/Administrators DACL helper is used on Windows. Symlinks and foreign-owned state are rejected. Writes are atomic. Backups must preserve the same access restrictions. `ca.pem` is the only public export.

The root lasts ten years and is never replaced automatically. Leaf certificates last 30 days; they renew within seven days of expiry or when the advertised SAN set changes. The server reloads its SSL context without a plaintext fallback. Normal renewal preserves both identity keys and all native pairings. TLS 1.2 is the minimum. Corrupt/missing identity metadata and a root approaching expiry fail closed with recovery instructions rather than minting a new trusted identity.

For deliberate identity rotation, stop the host and repeat its LAN options with `--lan-rotate-identity`:

```sh
termx --host 127.0.0.1 --lan-tls --lan-port 8788 --lan-name laptop.local --lan-rotate-identity
```

This replaces both keys. Native clients must explicitly forget the old TLS trust and scan the verified new QR. Browser users must remove the old CA and trust the new one. Rotation does not revoke application sessions: if a key or device credential was exposed, revoke affected sessions as well. A copied profile is a copied identity; do not clone private identity files to another installation.

## Native client boundary

The companion PR supplies an Expo local module for **both HTTPS and WSS**, including GraphQL, PTY, computer streams and the native bridge for editor LSP. It anchors TLS only at the CA deliberately accepted from the QR, still validates the chain, date and SAN, and stores the immutable host/CA binding in device-only SecureStore. A changed key or another host at the same saved address is rejected until explicit trust removal. Signed-in host IDs must match the TLS host ID. Address changes require scanning the same host's new QR; discovery never replaces trust.

Pairing tickets are short-lived, single-use and bound to the sponsoring live session. Their CA binding is added only for addresses this TLS listener advertises. The QR fragment is not a reusable login password. TLS is authenticated **before** any password, refresh credential or pairing ticket is transmitted. Raw keys and credentials are not sent to analytics or logs.

The native module uses Apple SecTrust with only the QR anchor and Android's PKIX trust manager with only that anchor plus OkHttp's default hostname verification. There is no trust-all callback, permissive hostname verifier or HTTP retry. Browser trust remains a separate path. Expo Go lacks this module and fails with an actionable message.

## WebSocket and proxy controls

Browser WebSocket Origins must exactly match the serving HTTPS origin, including the port, even when a bearer credential is supplied. Native clients omit Origin and still need authentication and scopes. Managed browser/native sockets use the existing exact-path, one-use admission tickets; idle revocation remains enforced by SessionGuard.

The LAN listener ignores forwarded headers. The loopback-only broker retains Uvicorn's explicitly loopback-limited proxy support for the existing locally launched Cloudflare/tunnel path. It is not a generic LAN reverse proxy. Access logs are disabled at listener startup to avoid recording socket proofs in request URLs. CLI banners and desktop-ready notifications omit shared passcodes; use authenticated Settings pairing instead.

## Troubleshooting

| Symptom | Action |
| --- | --- |
| Browser reports unknown issuer | Verify the host fingerprint; manually trust its **public** CA in the browser/OS that makes the request. |
| Hostname/IP mismatch | Use an advertised address. Add the resolving `.local` name or reachable private IP in Settings and restart. Never disable hostname checks. |
| Phone reports changed identity | Verify the host locally. If rotation was intentional, remove old TLS trust under the companion's Connections & Security, then scan the new QR. Otherwise stop and investigate. |
| Native session and TLS host differ | Sign out, verify the correct host and deliberately pair again. Do not reuse another host's session. |
| Address changed | Use the current LAN address or resolving `.local` name. The host refreshes IPv4 SANs; scan a new ticket from the same identity for a new address. |
| Listener cannot bind | Choose an available LAN port or valid local interface. No insecure fallback is opened. |
| Root expired, clock incorrect or private state damaged | Correct the clock or restore the private backup. If recovery is impossible, deliberately rotate and re-pair. |
| Expo Go or missing native module | Install a TermX development/production build containing `TermxLanTransport`. |
| Large native LAN file fails | The initial bounded native transport limits uploads to 24 MiB and responses to 32 MiB; use another authenticated transfer for larger files. |

## Threat model and verification limits

Protect against LAN observers, active interception, spoofed discovery/mDNS, substituted certificates, cross-origin browser socket attacks, ticket replay and accidental secret logging. Authentication and resource authorization remain independent of network discovery and TLS. Same-user host compromise, stolen unlocked devices, malicious user-approved QR codes, and compromised CA private keys remain outside TLS's protection. Browser root-management policy and multicast reachability vary by platform.

Linux tests use temporary CAs and explicit in-process client trust, without OS trust changes. They prove real OpenSSL/Uvicorn HTTPS/WSS verification, SAN rejection, wrong-CA rejection, Origin/auth rejection, persistence/renewal/rotation, private storage, advertisement and plaintext protection. See `test_lan_tls.py`, the existing managed-pairing tests and the evidence report.

**Release gates:** this environment has no Xcode/iOS SDK, Android SDK/Gradle or native devices. Swift/Kotlin compilation, installed-device camera scanning, native positive and negative TLS handshakes, background/keyboard behavior, and macOS/Windows storage ACL execution are not claimed. Expo autolinking, TypeScript and JS adapter tests are supporting checks, not substitutes for these gates.

Primary references: [Uvicorn settings](https://www.uvicorn.org/settings/), [PyCA X.509 tutorial](https://cryptography.io/en/latest/x509/tutorial/), [Expo Modules API](https://docs.expo.dev/modules/overview/), [Apple exclusive trust anchors](https://developer.apple.com/documentation/security/sectrustsetanchorcertificatesonly(_:_:)), [OkHttp HTTPS guidance](https://square.github.io/okhttp/features/https/).
