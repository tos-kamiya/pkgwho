import json

from pkgwho import cli


def test_desktop_search_names_filenames_precedence_and_direct_path(monkeypatch, tmp_path):
    user = tmp_path / "user" / "applications"
    system = tmp_path / "system" / "applications"
    user.mkdir(parents=True)
    system.mkdir(parents=True)
    (user / "example.desktop").write_text("[Desktop Entry]\nType=Application\nName=Screenshot\nName[ja]=スクリーンショット\n")
    (system / "example.desktop").write_text("[Desktop Entry]\nType=Application\nName=Old Screenshot\n")
    (system / "hidden.desktop").write_text("[Desktop Entry]\nType=Application\nHidden=true\nName=Screenshot\n")
    monkeypatch.setenv("XDG_DATA_HOME", str(user.parent))
    monkeypatch.setenv("XDG_DATA_DIRS", str(system.parent))
    monkeypatch.setattr(cli, "_desktop_packages", lambda path: [cli.PackageMatch("dpkg", "example", "sudo apt remove example", evidence="dpkg path record")])
    matches = cli.search_desktop("スクリーンショット")
    assert len(matches) == 1
    assert matches[0].name == "Screenshot"
    assert matches[0].matched_names == ["スクリーンショット"]
    assert matches[0].packages[0].package == "example"
    assert cli.search_desktop("AMPLE.desktop")[0].file == str(user / "example.desktop")
    assert cli.search_desktop(str(system / "example.desktop"))[0].name == "Old Screenshot"
    assert cli.search_desktop("Old Screenshot") == []


def test_desktop_cli_json_and_no_match(monkeypatch, capsys):
    monkeypatch.setattr(cli, "search_desktop", lambda query: [cli.DesktopMatch("Screenshot", ["スクリーンショット"], "/app.desktop")])
    assert cli.main(["--type", "desktop", "スクリーンショット", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["matches"][0]["name"] == "Screenshot"
    monkeypatch.setattr(cli, "search_desktop", lambda query: [])
    assert cli.main(["--type", "desktop", "missing"]) == 1
    assert "no desktop application matched" in capsys.readouterr().out


def test_desktop_manager_evidence(monkeypatch):
    monkeypatch.setattr(cli, "_dpkg_owner", lambda paths: None)
    monkeypatch.setattr(cli, "_brew_owner", lambda paths: None)
    monkeypatch.setattr(cli, "_snap_packages", lambda: {"example"})
    monkeypatch.setattr(cli, "_flatpak_apps", lambda: {"org.example.App"})
    assert cli._desktop_packages(cli.Path("/var/lib/snapd/desktop/applications/example_app.desktop"))[0].package == "example"
    assert cli._desktop_packages(cli.Path("/var/lib/flatpak/exports/share/applications/org.example.App.desktop"))[0].package == "org.example.App"
    assert cli._desktop_packages(cli.Path("/tmp/org.example.App.desktop")) == []
