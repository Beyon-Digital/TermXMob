from __future__ import annotations

import io
import socket

import qrcode


def lan_ipv4_addresses() -> list[str]:
    found: list[str] = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(0.3)
            sock.connect(("1.1.1.1", 80))
            found.append(sock.getsockname()[0])
    except OSError:
        pass
    out: list[str] = []
    for ip in found:
        if ip.startswith("127.") or ip in out:
            continue
        out.append(ip)
    return out


def http_urls(port: int, host: str = "0.0.0.0") -> list[str]:
    urls = [f"http://127.0.0.1:{port}"]
    if host not in {"0.0.0.0", "::", "127.0.0.1"}:
        urls.append(f"http://{host}:{port}")
    for ip in lan_ipv4_addresses():
        url = f"http://{ip}:{port}"
        if url not in urls:
            urls.append(url)
    return urls


def qr_ascii(data: str) -> str:
    qr = qrcode.QRCode(border=1)
    qr.add_data(data)
    qr.make(fit=True)
    buf = io.StringIO()
    qr.print_ascii(out=buf, invert=True)
    return buf.getvalue().rstrip()


def qr_svg(data: str, box_size: int = 8, border: int = 2) -> str:
    from qrcode.image.svg import SvgPathImage

    qr = qrcode.QRCode(image_factory=SvgPathImage, box_size=box_size, border=border)
    qr.add_data(data)
    qr.make(fit=True)
    buf = io.BytesIO()
    qr.make_image().save(buf)
    return buf.getvalue().decode("utf-8")


def connect_url(base: str, passcode: str | None) -> str:
    if not passcode:
        return base
    sep = "&" if "?" in base else "?"
    return f"{base}{sep}k={passcode}"
