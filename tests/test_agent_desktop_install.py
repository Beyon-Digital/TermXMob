"""Managed install of the pinned agent-desktop release: download, verify, extract, remove. No real network."""
from __future__ import annotations

import asyncio
import os
import tarfile

import pytest

from agent_desktop_release_server import (
    ASSET_NAME, MAC_ARM64, ReleaseServer, client_for, good_members, make_archive, pins_for, sha,
)
from termx.desktop import agent_desktop as ad
from termx.desktop import agent_desktop_install as install

posix_only = pytest.mark.skipif(os.name == "nt", reason="POSIX file modes")


@pytest.fixture
def server():
    release = ReleaseServer()
    yield release
    release.close()


def installer_for(client, server, **options):
    settings = {"base_url": server.base_url, "allowed_hosts": ("127.0.0.1",), "allow_http": True}
    settings.update(options)
    return install.Installer(client, **settings)


def status(client, refresh=True):
    return asyncio.run(client.status(refresh=refresh))


def setup(tmp_path, server, members=None, archive=None, **pin_options):
    members = good_members() if members is None else members
    archive = make_archive(members) if archive is None else archive
    client = client_for(tmp_path, pins_for(archive, members, **pin_options))
    server.serve_asset(archive)
    return client, installer_for(client, server), archive, members


def managed_dir(client):
    return client.bin_dir


def test_plan_comes_from_the_pins_and_the_url_is_built_not_taken_from_input():
    archive = make_archive(good_members())
    client = client_for("/unused", pins_for(archive))
    plan = install.Installer(client).plan()
    assert plan.asset == ASSET_NAME and plan.size == len(archive) and plan.sha256 == sha(archive)
    assert plan.url == f"https://github.com/vercel-labs/agent-desktop/releases/download/v1.2.0/{ASSET_NAME}"
    assert plan.host == "github.com"


def test_the_shipped_pins_plan_the_real_release_asset_for_macos_arm64():
    plan = install.Installer(ad.AgentDesktopClient(home_root="/unused", platform=MAC_ARM64)).plan()
    assert plan.url == "https://github.com/vercel-labs/agent-desktop/releases/download/v1.2.0/agent-desktop-v1.2.0-aarch64-apple-darwin.tar.gz"
    assert plan.size == 2230641 and plan.sha256 == "219813428bb1ad8decae172faed05c5964a2415170df402e2786ccfa6452ba82"


def test_a_platform_without_complete_pins_cannot_be_planned():
    archive = make_archive(good_members())
    client = client_for("/unused", pins_for(archive, files=None))
    with pytest.raises(install.InstallError) as caught:
        install.Installer(client).plan()
    assert caught.value.code == "pin_incomplete"


def test_install_downloads_verifies_extracts_and_the_client_becomes_ready(tmp_path, server):
    client, installer, archive, members = setup(tmp_path, server)
    assert status(client).installed is False

    installer.install()

    current = status(client)
    assert current.ready and current.source == "managed" and current.verified and current.version == "1.2.0"
    assert sorted(path.name for path in managed_dir(client).iterdir()) == sorted(members)
    for name, data in members.items():
        assert sha((managed_dir(client) / name).read_bytes()) == sha(data)
    assert server.requests == [f"127.0.0.1:{server.port}/releases/{ASSET_NAME}"]
    # Nothing else is left beside the version directory: no download or staging leftovers.
    assert [path.name for path in managed_dir(client).parent.iterdir()] == ["1.2.0"]


@posix_only
def test_installed_files_are_private_and_executable_by_the_owner(tmp_path, server):
    client, installer, _, members = setup(tmp_path, server)
    installer.install()
    assert (managed_dir(client).stat().st_mode & 0o077) == 0
    for name in members:
        assert (managed_dir(client) / name).stat().st_mode & 0o777 == 0o700


def test_a_wrong_checksum_installs_nothing(tmp_path, server):
    client, installer, archive, _ = setup(tmp_path, server)
    tampered = bytearray(archive)
    tampered[-1] ^= 0xFF  # same size, different hash
    server.serve_asset(bytes(tampered))
    with pytest.raises(install.InstallError) as caught:
        installer.install()
    assert caught.value.code == "hash_mismatch"
    assert not managed_dir(client).exists()
    assert list(managed_dir(client).parent.iterdir()) == []
    assert status(client).installed is False


@pytest.mark.parametrize("change", ["shorter", "longer"])
def test_a_wrong_size_installs_nothing(tmp_path, server, change):
    client, installer, archive, _ = setup(tmp_path, server)
    server.serve_asset(archive[:-10] if change == "shorter" else archive + b"x" * 50)
    with pytest.raises(install.InstallError) as caught:
        installer.install()
    assert caught.value.code == "size_mismatch"
    assert not managed_dir(client).exists() and list(managed_dir(client).parent.iterdir()) == []


def test_a_dropped_connection_installs_nothing(tmp_path, server):
    client, installer, archive, _ = setup(tmp_path, server)
    server.serve("/releases/" + ASSET_NAME, archive[:100], declared_length=len(archive))
    with pytest.raises(install.InstallError) as caught:
        installer.install()
    assert caught.value.code in {"download_failed", "size_mismatch"}
    assert not managed_dir(client).exists() and list(managed_dir(client).parent.iterdir()) == []


def test_a_missing_asset_is_a_download_failure(tmp_path, server):
    client, installer, _, _ = setup(tmp_path, server)
    server.routes.clear()
    with pytest.raises(install.InstallError) as caught:
        installer.install()
    assert caught.value.code == "download_failed"
    assert not managed_dir(client).exists()


def _bad_archives():
    good = good_members()
    cli, helper = good["agent-desktop"], good["agent-desktop-macos-helper"]
    regular = {}
    link = {"type": tarfile.SYMTYPE, "linkname": "/etc/passwd"}
    directory = {"type": tarfile.DIRTYPE}
    return {
        "extra_member": [("agent-desktop", cli, regular), ("agent-desktop-macos-helper", helper, regular), ("README.md", b"hi", regular)],
        "link_member": [("agent-desktop", cli, regular), ("agent-desktop-macos-helper", None, link)],
        "parent_traversal": [("agent-desktop", cli, regular), ("agent-desktop-macos-helper", helper, regular), ("../evil", b"x", regular)],
        "absolute_name": [("/tmp/agent-desktop-evil", cli, regular), ("agent-desktop-macos-helper", helper, regular)],
        "nested_name": [("bin/agent-desktop", cli, regular), ("agent-desktop-macos-helper", helper, regular)],
        "duplicate_member": [("agent-desktop", cli, regular), ("agent-desktop", cli, regular), ("agent-desktop-macos-helper", helper, regular)],
        "directory_member": [("agent-desktop", cli, regular), ("agent-desktop-macos-helper", None, directory)],
        "missing_member": [("agent-desktop", cli, regular)],
    }


@pytest.mark.parametrize("case", sorted(_bad_archives()))
def test_a_hostile_archive_with_a_matching_checksum_still_installs_nothing(tmp_path, server, case):
    members = good_members()
    archive = make_archive(_bad_archives()[case])
    client, installer, _, _ = setup(tmp_path, server, members=members, archive=archive)
    with pytest.raises(install.InstallError) as caught:
        installer.install()
    assert caught.value.code == "bad_archive"
    assert not managed_dir(client).exists()
    assert list(managed_dir(client).parent.iterdir()) == []
    assert not (tmp_path / "evil").exists() and not (tmp_path / "desktop-control" / "evil").exists()


def test_a_member_over_the_size_limit_is_refused(tmp_path, server, monkeypatch):
    monkeypatch.setattr(install, "MAX_MEMBER_BYTES", 16)
    client, installer, _, _ = setup(tmp_path, server)
    with pytest.raises(install.InstallError) as caught:
        installer.install()
    assert caught.value.code == "bad_archive" and not managed_dir(client).exists()


def test_a_member_whose_hash_differs_from_its_pin_is_refused(tmp_path, server):
    members = good_members()
    archive = make_archive(members)
    wrong = {**{name: sha(data) for name, data in members.items()}, "agent-desktop": "0" * 64}
    client, installer, _, _ = setup(tmp_path, server, members=members, archive=archive, files=wrong)
    with pytest.raises(install.InstallError) as caught:
        installer.install()
    assert caught.value.code == "hash_mismatch" and not managed_dir(client).exists()
    assert list(managed_dir(client).parent.iterdir()) == []


def test_the_installer_follows_redirects_inside_the_allowed_hosts(tmp_path, server):
    client, installer, archive, _ = setup(tmp_path, server)
    server.redirect("/releases/" + ASSET_NAME, f"http://127.0.0.1:{server.port}/cdn/one")
    server.redirect("/cdn/one", f"http://127.0.0.1:{server.port}/cdn/two")
    server.serve("/cdn/two", archive)
    installer.install()
    assert status(client).ready
    assert len(server.requests) == 3


def test_a_redirect_to_another_host_is_refused_before_it_is_requested(tmp_path, server):
    client, installer, archive, _ = setup(tmp_path, server)
    server.redirect("/releases/" + ASSET_NAME, f"http://localhost:{server.port}/stolen")
    server.serve("/stolen", archive)
    with pytest.raises(install.InstallError) as caught:
        installer.install()
    assert caught.value.code == "download_failed"
    assert server.requests == [f"127.0.0.1:{server.port}/releases/{ASSET_NAME}"]
    assert not managed_dir(client).exists()


@pytest.mark.parametrize("target", ["file:///etc/passwd", "ftp://127.0.0.1/x", "//localhost/x"])
def test_a_redirect_to_another_scheme_is_refused(tmp_path, server, target):
    client, installer, _, _ = setup(tmp_path, server)
    server.redirect("/releases/" + ASSET_NAME, target)
    with pytest.raises(install.InstallError) as caught:
        installer.install()
    assert caught.value.code == "download_failed" and not managed_dir(client).exists()


def test_more_than_three_redirects_are_refused(tmp_path, server):
    client, installer, archive, _ = setup(tmp_path, server)
    server.redirect("/releases/" + ASSET_NAME, f"http://127.0.0.1:{server.port}/r1")
    for index in range(1, 4):
        server.redirect(f"/r{index}", f"http://127.0.0.1:{server.port}/r{index + 1}")
    server.serve("/r4", archive)
    with pytest.raises(install.InstallError) as caught:
        installer.install()
    assert caught.value.code == "download_failed" and "/r4" not in " ".join(server.requests)


def test_plain_http_is_refused_unless_the_test_seam_allows_it(tmp_path, server):
    client, _, _, _ = setup(tmp_path, server)
    strict = install.Installer(client, base_url=server.base_url, allowed_hosts=("127.0.0.1",))
    with pytest.raises(install.InstallError) as caught:
        strict.install()
    assert caught.value.code == "download_failed" and server.requests == []


def test_reinstalling_replaces_a_tampered_copy_and_a_second_install_is_harmless(tmp_path, server):
    client, installer, _, members = setup(tmp_path, server)
    installer.install()
    installer.install()
    assert status(client).ready
    (managed_dir(client) / "agent-desktop").write_bytes(b"print('evil')")
    assert status(client).ready is False and status(client).issue == "hash_mismatch"
    installer.install()
    assert status(client).ready
    assert sha((managed_dir(client) / "agent-desktop").read_bytes()) == sha(members["agent-desktop"])


def test_a_file_changed_after_install_is_refused_on_the_next_start(tmp_path, server):
    client, installer, archive, members = setup(tmp_path, server)
    installer.install()
    assert status(client).ready
    (managed_dir(client) / "agent-desktop").write_bytes(b"print('evil')")
    restarted = client_for(tmp_path, pins_for(archive, members))
    current = asyncio.run(restarted.status())
    assert current.ready is False and current.issue == "hash_mismatch"
    with pytest.raises(ad.AgentDesktopFailure):
        asyncio.run(restarted.run(ad.req_status()))


def test_remove_deletes_only_the_managed_version_directory(tmp_path, server):
    client, installer, _, _ = setup(tmp_path, server)
    installer.install()
    other = managed_dir(client).parent / "0.9.0"
    other.mkdir()
    (other / "keep").write_text("x")
    home = client.home_for("someone")
    (home / "keep").write_text("x")
    killed = []
    client.kill_all = lambda: killed.append(True)  # type: ignore[method-assign]

    assert installer.remove() is True

    assert not managed_dir(client).exists() and (other / "keep").exists() and (home / "keep").exists()
    assert killed == [True]
    current = status(client)
    assert current.installed is False and current.ready is False


def test_remove_when_nothing_is_installed_is_a_no_op(tmp_path, server):
    client, installer, _, _ = setup(tmp_path, server)
    assert installer.remove() is False


@posix_only
def test_remove_never_follows_a_symlink_out_of_the_managed_directory(tmp_path, server):
    client, installer, _, _ = setup(tmp_path, server)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "precious").write_text("x")
    managed_dir(client).parent.mkdir(parents=True)
    managed_dir(client).symlink_to(outside, target_is_directory=True)
    installer.remove()
    assert (outside / "precious").exists() and not managed_dir(client).exists()


def test_make_installer_uses_the_pinned_github_release_with_https_only():
    client = ad.AgentDesktopClient(home_root="/unused", platform=MAC_ARM64)
    plan = install.make_installer(client).plan()
    assert plan.url.startswith("https://github.com/vercel-labs/agent-desktop/releases/download/v1.2.0/")
    assert install.ALLOWED_HOSTS == ("github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com")
    assert install.MAX_REDIRECTS == 3
