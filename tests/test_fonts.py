from pkgwho import cli


def test_font_search_identifies_owners_and_unowned_files(monkeypatch, tmp_path):
    owned = tmp_path / "owned.ttf"
    unowned = tmp_path / "personal.ttf"
    calls = []

    def run(args):
        calls.append(args)
        if args[0] == "fc-list":
            return (
                f"Noto Sans,ノトサンズ\tRegular\tNoto Sans Regular\t{owned}\n"
                f"Noto Sans\tBold\tNoto Sans Bold\t{unowned}\n"
                f"Noto Sans\tBold\tNoto Sans Bold\t{unowned}\n"
                f"Other\tRegular\tOther Regular\t{owned}"
            )
        if args[0] == "dpkg-query" and args[-1] == str(owned):
            return f"fonts-noto: {owned}"
        return None

    monkeypatch.setattr(cli, "_run", run)
    cli._brew_owner.cache_clear()
    matches = cli.search_fonts("noto-sans")
    assert matches is not None and len(matches) == 2
    assert matches[0].file == str(unowned)
    assert matches[0].packages == []
    assert matches[1].packages[0].package == "fonts-noto"
    assert sum(call[0] == "dpkg-query" for call in calls) == 2
    cli._brew_owner.cache_clear()


def test_font_cli_json_and_failures(monkeypatch, capsys):
    monkeypatch.setattr(cli, "search_fonts", lambda query: [cli.FontMatch("Example", "Regular", "Example Regular", "/fonts/example.ttf")])
    assert cli.main(["--type", "font", "example", "--json"]) == 0
    assert '"file": "/fonts/example.ttf"' in capsys.readouterr().out
    monkeypatch.setattr(cli, "search_fonts", lambda query: None)
    assert cli.main(["--type", "font", "missing"]) == 1
    assert "fc-list is unavailable" in capsys.readouterr().out
    monkeypatch.setattr(cli, "search_fonts", lambda query: [])
    assert cli.main(["--type", "font", "missing"]) == 1
    assert "no installed font matched" in capsys.readouterr().out


def test_search_type_selection_and_combined_results(monkeypatch, capsys):
    import json

    calls = []

    def inspect(query):
        calls.append("command")
        return cli.Result(query, None, None, "unknown")

    def fonts(query):
        calls.append("font")
        return [cli.FontMatch("Example", "Regular", "Example Regular", "/font.ttf")]

    monkeypatch.setattr(cli, "inspect", inspect)
    def packages(query):
        calls.append("package")
        return []

    monkeypatch.setattr(cli, "_search_installed_packages", packages)
    monkeypatch.setattr(cli, "search_fonts", fonts)
    assert cli.main(["example", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)[0]
    assert calls == ["command", "package", "font"]
    assert result["query"] == "example"
    assert result["font"]["matches"][0]["file"] == "/font.ttf"
    assert result["command"]["executable"] is None

    calls.clear()
    assert cli.main(["--type", "package", "example"]) == 1
    assert calls == ["package"]
    capsys.readouterr()
    calls.clear()
    assert cli.main(["--type", "command", "example"]) == 1
    assert calls == ["command"]
    capsys.readouterr()
    calls.clear()
    assert cli.main(["--type", "font", "example"]) == 0
    assert calls == ["font"]
    capsys.readouterr()
    calls.clear()
    assert cli.main(["--type", "all", "example"]) == 0
    assert calls == ["command", "package", "font"]
    assert "Command search:" in capsys.readouterr().out


def test_combined_command_success_and_font_unavailable(monkeypatch, capsys):
    monkeypatch.setattr(cli, "inspect", lambda query: cli.Result(query, "/bin/tool", "/bin/tool", "unknown"))
    monkeypatch.setattr(cli, "search_fonts", lambda query: None)
    assert cli.main(["tool"]) == 0
    assert "fc-list is unavailable" in capsys.readouterr().out
    monkeypatch.setattr(cli, "inspect", lambda query: cli.Result(query, None, None, "unknown"))
    monkeypatch.setattr(cli, "_search_installed_packages", lambda query: [])
    assert cli.main(["tool"]) == 1


def test_package_search_includes_libraries_and_runs_for_resolved_commands(monkeypatch, capsys):
    import json

    monkeypatch.setattr(cli, "inspect", lambda query: cli.Result(query, "/bin/tool", "/bin/tool", "unknown"))
    monkeypatch.setattr(cli, "_search_installed_packages", lambda query: [
        cli.PackageMatch("dpkg", "libexample", "sudo apt remove libexample", section="libs")
    ])
    monkeypatch.setattr(cli, "search_fonts", lambda query: [])
    assert cli.main(["tool", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)[0]
    assert result["package"]["matches"][0]["package"] == "libexample"
    assert result["command"]["matches"] == []
    assert cli.main(["--type", "package", "tool"]) == 0
    assert "libexample" in capsys.readouterr().out
