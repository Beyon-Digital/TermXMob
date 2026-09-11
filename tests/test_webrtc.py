from termx.desktop.webrtc import AiortcBackend, DEFAULT_ICE_SERVERS, RtcError, RtcManager, RtcSession


class FakeBackend:
    def __init__(self) -> None:
        self.offers: list[tuple[str, dict]] = []
        self.ice: list[tuple[str, dict]] = []
        self.closed: list[str] = []

    def handle_offer(self, session_id: str, offer: dict) -> dict:
        self.offers.append((session_id, offer))
        return {"type": "answer", "sdp": "v=0\r\n"}

    def add_ice(self, session_id: str, candidate: dict) -> None:
        self.ice.append((session_id, candidate))

    def close(self, session_id: str) -> None:
        self.closed.append(session_id)


class RealBackend(FakeBackend):
    available = True


def test_available_false_by_default() -> None:
    assert RtcManager().available() is False
    assert RtcManager(backend=FakeBackend()).available() is False


def test_available_true_only_for_real_backend() -> None:
    assert RtcManager(backend=RealBackend()).available() is True


def test_create_session_returns_ice_servers() -> None:
    mgr = RtcManager(backend=FakeBackend())
    created = mgr.create_session("s1")
    assert created["session_id"] == "s1"
    assert created["ice_servers"] == DEFAULT_ICE_SERVERS
    assert created["ice_servers"] == [{"urls": "stun:stun.cloudflare.com:3478"}]
    assert isinstance(RtcSession(session_id="s1"), RtcSession)


def test_handle_offer_returns_answer() -> None:
    backend = FakeBackend()
    mgr = RtcManager(backend=backend)
    mgr.create_session("s1")
    result = mgr.handle_offer("s1", {"type": "offer", "sdp": "v=0\r\n"})
    assert result["session_id"] == "s1"
    assert result["answer"] == {"type": "answer", "sdp": "v=0\r\n"}
    assert backend.offers == [("s1", {"type": "offer", "sdp": "v=0\r\n"})]


def test_handle_offer_without_backend_raises() -> None:
    mgr = RtcManager()
    mgr.create_session("s1")
    try:
        mgr.handle_offer("s1", {"type": "offer", "sdp": "v=0"})
        raise AssertionError("expected RtcError")
    except RtcError:
        pass


def test_close_all_clears() -> None:
    backend = FakeBackend()
    mgr = RtcManager(backend=backend)
    mgr.create_session("a")
    mgr.create_session("b")
    mgr.add_ice("a", {"candidate": "x"})
    mgr.close_all()
    assert set(backend.closed) == {"a", "b"}
    mgr.close_all()
    assert backend.closed.count("a") == 1
    assert backend.closed.count("b") == 1
    mgr.add_ice("a", {"candidate": "late"})
    assert backend.ice == [("a", {"candidate": "x"})]
    created = mgr.create_session("a")
    assert created["session_id"] == "a"


def test_default_unavailable_without_aiortc_injected_backend_still_works() -> None:
    assert AiortcBackend().available is False
    assert RtcManager().available() is False
    backend = RealBackend()
    mgr = RtcManager(backend=backend)
    assert mgr.available() is True
    mgr.create_session("s1")
    result = mgr.handle_offer("s1", {"type": "offer", "sdp": "v=0\r\n"})
    assert result["answer"] == {"type": "answer", "sdp": "v=0\r\n"}
    assert backend.offers == [("s1", {"type": "offer", "sdp": "v=0\r\n"})]
