from __future__ import annotations

import asyncio
import io
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

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
    principal_id: str | None = None
    managed_session_id: str | None = None
    authorize: Callable[[], bool] | None = None


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


def _screen_track(fps: int = 12, authorize: Callable[[], bool] | None = None) -> Any:
    base = _VideoStreamTrack
    if base is None:
        raise RtcError("WebRTC backend unavailable")

    class ScreenTrack(base):
        def __init__(self) -> None:
            super().__init__()
            self.target_fps = max(1, min(30, int(fps)))
            self._last_frame_at = 0.0

        async def recv(self) -> Any:
            loop = asyncio.get_running_loop()
            remaining = (1.0 / self.target_fps) - (loop.time() - self._last_frame_at)
            if remaining > 0:
                await asyncio.sleep(remaining)
            pts, time_base = await self.next_timestamp()
            from termx.desktop.capture import grab_jpeg

            from termx.desktop.recording import capture_privacy_revision
            def permitted():
                if authorize is None or not authorize():
                    raise PermissionError("RTC view authority expired")
                return capture_privacy_revision()
            try:
                revision = await asyncio.to_thread(permitted)
                jpeg = await asyncio.to_thread(grab_jpeg)
                # A private barrier set and removed during capture still changes
                # the epoch. Never publish that captured frame after recovery.
                if await asyncio.to_thread(permitted) != revision:
                    raise PermissionError("RTC privacy changed during capture")
                frame = await asyncio.to_thread(_jpeg_to_video_frame, jpeg)
                if await asyncio.to_thread(permitted) != revision:
                    raise PermissionError("RTC privacy changed before publication")
            except PermissionError as exc:
                self.stop()
                raise RtcError(str(exc)) from exc
            if frame is None:
                raise RtcError("video frame conversion unavailable")
            frame.pts = pts
            frame.time_base = time_base
            self._last_frame_at = loop.time()
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
        self._tracks: dict[str, Any] = {}
        self._target_fps: dict[str, int] = {}
        self._guards: dict[str, Callable[[], bool]] = {}
        self._watchers: dict[str, Any] = {}

    def set_authorizer(self, session_id: str, authorize: Callable[[], bool]) -> None:
        self._guards[session_id] = authorize

    def handle_offer(self, session_id: str, offer: dict[str, Any]) -> dict[str, Any]:
        if not self.available or _RTCPeerConnection is None or _RTCSessionDescription is None:
            raise RtcError("WebRTC backend unavailable")
        target_fps = self._target_fps.get(session_id, 12)
        guard = self._guards.get(session_id)
        self.close(session_id)
        if guard is not None: self._guards[session_id] = guard
        self._target_fps[session_id] = target_fps
        return _run_coro(self._handle_offer(session_id, offer))

    async def _handle_offer(self, session_id: str, offer: dict[str, Any]) -> dict[str, Any]:
        pc = _RTCPeerConnection()
        track = _screen_track(self._target_fps.get(session_id, 12), self._guards.get(session_id))
        pc.addTrack(track)
        self._tracks[session_id] = track
        self._pcs[session_id] = pc
        async def watch():
            guard = self._guards.get(session_id)
            while self._pcs.get(session_id) is pc:
                await asyncio.sleep(0.25)
                try:
                    from termx.desktop.recording import capture_privacy_revision
                    def check():
                        capture_privacy_revision()
                        return guard is not None and guard()
                    allowed = await asyncio.to_thread(check)
                except Exception:
                    allowed = False
                if not allowed:
                    track.stop()
                    await pc.close()
                    break
        self._watchers[session_id] = asyncio.create_task(watch())
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
        track = self._tracks.pop(session_id, None)
        if track is not None: track.stop()
        watcher = self._watchers.pop(session_id, None)
        if watcher is not None: watcher.get_loop().call_soon_threadsafe(watcher.cancel)
        self._guards.pop(session_id, None)
        self._target_fps.pop(session_id, None)
        pc = self._pcs.get(session_id)
        if pc is None:
            return
        try:
            closer = pc.close()
            if asyncio.iscoroutine(closer):
                _run_coro(closer)
        except Exception as exc:
            raise RtcError("RTC peer shutdown could not be confirmed") from exc
        self._pcs.pop(session_id,None)

    def set_fps(self, session_id: str, fps: int) -> None:
        self._target_fps[session_id] = max(1, min(30, int(fps)))
        track = self._tracks.get(session_id)
        if track is not None:
            track.target_fps = self._target_fps[session_id]


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
        self._target_fps: dict[str, int] = {}
        from termx.desktop.recording import on_capture_private
        on_capture_private(self.close_all)

    def available(self) -> bool:
        backend = self._backend
        if backend is None:
            return False
        flag = getattr(backend, "available", False)
        return bool(flag() if callable(flag) else flag)

    def create_session(self, session_id: str, *, principal_id=None, managed_session_id=None, authorize=None) -> dict[str, Any]:
        if not isinstance(session_id,str) or not 1<=len(session_id)<=128:raise RtcError("Invalid RTC identifier")
        if session_id not in self._sessions and (len(self._sessions)>=100 or sum(row.managed_session_id==managed_session_id and row.principal_id==principal_id for row in self._sessions.values())>=8):
            raise RtcError("Close an existing RTC connection before opening another")
        previous = self._sessions.get(session_id)
        if previous and (previous.principal_id, previous.managed_session_id) != (principal_id, managed_session_id):
            raise RtcError("RTC session belongs to another device")
        if session_id in self._sessions:
            self.close(session_id)
        session = RtcSession(session_id=session_id, ice_servers=list(self._ice_servers), principal_id=principal_id, managed_session_id=managed_session_id, authorize=authorize)
        self._sessions[session_id] = session
        return {"session_id": session_id, "ice_servers": list(session.ice_servers)}

    def handle_offer(self, session_id: str, offer: dict[str, Any], *, principal_id=None, managed_session_id=None, authorize=None) -> dict[str, Any]:
        session = self._sessions.get(session_id)
        if session is None or session.closed:
            self.create_session(session_id, principal_id=principal_id, managed_session_id=managed_session_id, authorize=authorize)
            session = self._sessions[session_id]
        self._owned(session, principal_id, managed_session_id)
        if authorize is not None: session.authorize = authorize
        if managed_session_id is not None and (session.authorize is None or not session.authorize()):
            raise RtcError("RTC view authority expired")
        setter = getattr(self._backend, "set_authorizer", None)
        if callable(setter): setter(session_id, session.authorize or (lambda: False))
        from termx.desktop.recording import capture_privacy_revision
        try:privacy=capture_privacy_revision()
        except PermissionError as exc:raise RtcError("Computer observation is private") from exc
        requested_fps = offer.get("_termx_fps")
        offer = {key: value for key, value in offer.items() if key != "_termx_fps"}
        if requested_fps is not None:
            self.set_fps(session_id, int(requested_fps))
        session.offer = offer
        if self._backend is None:
            raise RtcError("WebRTC backend unavailable")
        answer = self._backend.handle_offer(session_id, offer)
        try:
            if self._sessions.get(session_id) is not session or session.closed or capture_privacy_revision()!=privacy or (session.authorize is not None and not session.authorize()):
                raise PermissionError("RTC authority or privacy changed during negotiation")
        except PermissionError as exc:
            self._backend.close(session_id)
            self._sessions.pop(session_id,None)
            raise RtcError(str(exc)) from exc
        session.answer = answer
        return {"answer": answer, "session_id": session_id}

    def set_fps(self, session_id: str, fps: int) -> None:
        target = max(1, min(30, int(fps)))
        self._target_fps[session_id] = target
        setter = getattr(self._backend, "set_fps", None)
        if callable(setter):
            setter(session_id, target)

    def add_ice(self, session_id: str, candidate: dict[str, Any], *, principal_id=None, managed_session_id=None) -> None:
        session = self._sessions.get(session_id)
        if session is None or session.closed:
            return
        self._owned(session, principal_id, managed_session_id)
        if session.authorize is not None and not session.authorize():
            self.close(session_id)
            raise RtcError("RTC view authority expired")
        if len(session.candidates)>=256 or len(str(candidate.get("candidate", "")))>8192:raise RtcError("RTC candidate limit exceeded")
        session.candidates.append(candidate)
        if self._backend is not None:
            self._backend.add_ice(session_id, candidate)

    def close(self, session_id: str) -> None:
        self._target_fps.pop(session_id, None)
        session = self._sessions.get(session_id)
        if session is None:
            return
        session.closed = True
        if self._backend is not None:
            self._backend.close(session_id)
        self._sessions.pop(session_id,None)

    @staticmethod
    def _owned(session, principal_id, managed_session_id):
        if (session.principal_id, session.managed_session_id) != (principal_id, managed_session_id):
            raise RtcError("RTC session belongs to another device")

    def close_owned(self, session_id, *, principal_id=None, managed_session_id=None):
        session = self._sessions.get(session_id)
        if session is not None:
            self._owned(session, principal_id, managed_session_id)
            self.close(session_id)

    def close_device(self, principal_id, managed_session_id):
        for identifier, session in list(self._sessions.items()):
            if (session.principal_id,session.managed_session_id) == (principal_id,managed_session_id):
                self.close(identifier)

    def close_all(self) -> None:
        failures=[]
        for session_id in list(self._sessions):
            try:self.close(session_id)
            except Exception as exc:failures.append(exc)
        if failures:raise RtcError("RTC peer shutdown could not be confirmed") from failures[0]
