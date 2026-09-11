from __future__ import annotations

import asyncio
import io
import threading
from dataclasses import dataclass, field
from typing import Any, Protocol

DEFAULT_ICE_SERVERS: list[dict[str, str]] = [{"urls": "stun:stun.cloudflare.com:3478"}]

_RTCIceCandidate: Any = None
_RTCPeerConnection: Any = None
_RTCSessionDescription: Any = None
_VideoStreamTrack: Any = None
_av: Any = None
_numpy: Any = None

try:
    from aiortc import RTCIceCandidate, RTCPeerConnection, RTCSessionDescription, VideoStreamTrack

    _RTCIceCandidate = RTCIceCandidate
    _RTCPeerConnection = RTCPeerConnection
    _RTCSessionDescription = RTCSessionDescription
    _VideoStreamTrack = VideoStreamTrack
except ImportError:
    pass

try:
    import av as _av_mod

    _av = _av_mod
except ImportError:
    pass

try:
    import numpy as _numpy_mod

    _numpy = _numpy_mod
except ImportError:
    pass

_AIORTC_IMPORTED = _RTCPeerConnection is not None

_loop_lock = threading.Lock()
_rtc_loop: asyncio.AbstractEventLoop | None = None


class RtcError(RuntimeError):
    pass


class RtcBackend(Protocol):
    def handle_offer(self, session_id: str, offer: dict[str, Any]) -> dict[str, Any]: ...

    def add_ice(self, session_id: str, candidate: dict[str, Any]) -> None: ...

    def close(self, session_id: str) -> None: ...


@dataclass
class RtcSession:
    session_id: str
    ice_servers: list[dict[str, str]] = field(default_factory=lambda: list(DEFAULT_ICE_SERVERS))
    offer: dict[str, Any] | None = None
    answer: dict[str, Any] | None = None
    candidates: list[dict[str, Any]] = field(default_factory=list)
    closed: bool = False


class LoopbackBackend:
    available = False

    def handle_offer(self, session_id: str, offer: dict[str, Any]) -> dict[str, Any]:
        sdp = str(offer.get("sdp") or "v=0\r\n")
        return {"type": "answer", "sdp": sdp}

    def add_ice(self, session_id: str, candidate: dict[str, Any]) -> None:
        return None

    def close(self, session_id: str) -> None:
        return None


def _get_rtc_loop() -> asyncio.AbstractEventLoop:
    global _rtc_loop
    with _loop_lock:
        if _rtc_loop is None or _rtc_loop.is_closed():
            loop = asyncio.new_event_loop()
            threading.Thread(target=loop.run_forever, name="termx-webrtc", daemon=True).start()
            _rtc_loop = loop
        return _rtc_loop


def _run_coro(coro: Any) -> Any:
    loop = _get_rtc_loop()
    return asyncio.run_coroutine_threadsafe(coro, loop).result(timeout=15)


def _jpeg_to_video_frame(jpeg: bytes) -> Any:
    if _av is None:
        return None
    container = _av.open(io.BytesIO(jpeg))
    try:
        frame = next(container.decode(video=0))
    finally:
        container.close()
    if _numpy is None:
        return frame
    array = frame.to_ndarray(format="bgr24")
    return _av.VideoFrame.from_ndarray(array, format="bgr24")


def _screen_track() -> Any:
    base = _VideoStreamTrack
    if base is None:
        raise RtcError("WebRTC backend unavailable")

    class ScreenTrack(base):
        async def recv(self) -> Any:
            pts, time_base = await self.next_timestamp()
            from termx.desktop.capture import grab_jpeg

            jpeg = await asyncio.to_thread(grab_jpeg)
            frame = _jpeg_to_video_frame(jpeg)
            if frame is None:
                raise RtcError("video frame conversion unavailable")
            frame.pts = pts
            frame.time_base = time_base
            return frame

    return ScreenTrack()


def _ice_candidate(candidate: dict[str, Any]) -> Any:
    if _RTCIceCandidate is None:
        return None
    raw = candidate.get("candidate")
    if not raw:
        return None
    try:
        from aiortc.sdp import candidate_from_sdp

        text = str(raw)
        if text.startswith("candidate:"):
            text = text.split(":", 1)[1]
        ice = candidate_from_sdp(text)
        ice.sdpMid = candidate.get("sdpMid")
        ice.sdpMLineIndex = candidate.get("sdpMLineIndex")
        return ice
    except Exception:
        return None


class AiortcBackend:
    available = _AIORTC_IMPORTED

    def __init__(self) -> None:
        self._pcs: dict[str, Any] = {}

    def handle_offer(self, session_id: str, offer: dict[str, Any]) -> dict[str, Any]:
        if not self.available or _RTCPeerConnection is None or _RTCSessionDescription is None:
            raise RtcError("WebRTC backend unavailable")
        self.close(session_id)
        return _run_coro(self._handle_offer(session_id, offer))

    async def _handle_offer(self, session_id: str, offer: dict[str, Any]) -> dict[str, Any]:
        pc = _RTCPeerConnection()
        pc.addTrack(_screen_track())
        self._pcs[session_id] = pc
        remote = _RTCSessionDescription(sdp=str(offer.get("sdp") or ""), type=str(offer.get("type") or "offer"))
        await pc.setRemoteDescription(remote)
        answer = await pc.createAnswer()
        await pc.setLocalDescription(answer)
        local = pc.localDescription
        return {"type": local.type, "sdp": local.sdp}

    def add_ice(self, session_id: str, candidate: dict[str, Any]) -> None:
        pc = self._pcs.get(session_id)
        if pc is None or not self.available:
            return
        try:
            _run_coro(self._add_ice(pc, candidate))
        except Exception:
            return

    async def _add_ice(self, pc: Any, candidate: dict[str, Any]) -> None:
        parsed = _ice_candidate(candidate)
        if parsed is not None:
            await pc.addIceCandidate(parsed)
            return
        try:
            await pc.addIceCandidate(candidate)
        except Exception:
            return

    def close(self, session_id: str) -> None:
        pc = self._pcs.pop(session_id, None)
        if pc is None:
            return
        try:
            closer = pc.close()
            if asyncio.iscoroutine(closer):
                _run_coro(closer)
        except Exception:
            return


class RtcManager:
    def __init__(
        self,
        backend: Any | None = None,
        ice_servers: list[dict[str, str]] | None = None,
    ) -> None:
        if backend is None:
            candidate = AiortcBackend()
            backend = candidate if candidate.available else None
        self._backend = backend
        self._ice_servers = list(ice_servers or DEFAULT_ICE_SERVERS)
        self._sessions: dict[str, RtcSession] = {}

    def available(self) -> bool:
        backend = self._backend
        if backend is None:
            return False
        flag = getattr(backend, "available", False)
        return bool(flag() if callable(flag) else flag)

    def create_session(self, session_id: str) -> dict[str, Any]:
        if session_id in self._sessions:
            self.close(session_id)
        session = RtcSession(session_id=session_id, ice_servers=list(self._ice_servers))
        self._sessions[session_id] = session
        return {"session_id": session_id, "ice_servers": list(session.ice_servers)}

    def handle_offer(self, session_id: str, offer: dict[str, Any]) -> dict[str, Any]:
        session = self._sessions.get(session_id)
        if session is None or session.closed:
            self.create_session(session_id)
            session = self._sessions[session_id]
        session.offer = offer
        if self._backend is None:
            raise RtcError("WebRTC backend unavailable")
        answer = self._backend.handle_offer(session_id, offer)
        session.answer = answer
        return {"answer": answer, "session_id": session_id}

    def add_ice(self, session_id: str, candidate: dict[str, Any]) -> None:
        session = self._sessions.get(session_id)
        if session is None or session.closed:
            return
        session.candidates.append(candidate)
        if self._backend is not None:
            self._backend.add_ice(session_id, candidate)

    def close(self, session_id: str) -> None:
        session = self._sessions.pop(session_id, None)
        if session is None:
            return
        session.closed = True
        if self._backend is not None:
            self._backend.close(session_id)

    def close_all(self) -> None:
        for session_id in list(self._sessions):
            self.close(session_id)
