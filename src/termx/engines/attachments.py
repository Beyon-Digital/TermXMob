"""Bounded image wire payload shared by all native engine adapters."""
from __future__ import annotations
import base64


def image_attachments(attachments):
    from termx.agent.manager import _decode_images
    return [{'name':name,'mime':mime,'data':base64.b64encode(data).decode('ascii')}
            for name,mime,data in _decode_images(attachments)]
