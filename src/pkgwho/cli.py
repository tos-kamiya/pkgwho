# SPDX-FileCopyrightText: 2026-present Toshihiro Kamiya <kamiya@mbj.nifty.com>
#
# SPDX-License-Identifier: MIT
"""Command line interface for identifying package ownership."""

from __future__ import annotations

import argparse
import configparser
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Sequence

from pkgwho.__about__ import __version__
from pkgwho.shell_config import PathSetup, find_path_setup


@dataclass
class Result:
    command: str
    executable: str | None
    realpath: str | None
    manager: str
    package: str | None = None
    uninstall: str | None = None
    note: str | None = None
    matches: list[PackageMatch] = field(default_factory=list)
    path_setup: list[PathSetup] = field(default_factory=list)


@dataclass
class PackageMatch:
    manager: str
    package: str
    uninstall: str
    display_name: str | None = None
    evidence: str = "name match"
    section: str | None = None


@dataclass
class FontMatch:
    family: str
    style: str
    fullname: str
    file: str
    packages: list[PackageMatch] = field(default_factory=list)


@dataclass
class DesktopMatch:
    name: str
    matched_names: list[str]
    file: str
    packages: list[PackageMatch] = field(default_factory=list)


def _desktop_files() -> list[Path]:
    data_home = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local/share")
    data_dirs = os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share"
    seen: set[str] = set()
    files: list[Path] = []
    for root in [data_home, *data_dirs.split(":")]:
        if not root or not Path(root).is_absolute():
            continue
        directory = Path(root) / "applications"
        for path in sorted(directory.rglob("*.desktop")):
            desktop_id = "-".join(path.relative_to(directory).parts)
            if desktop_id not in seen:
                seen.add(desktop_id)
                files.append(path)
    return files


class _DesktopParser(configparser.ConfigParser):
    def optionxform(self, optionstr: str) -> str:
        return optionstr


def _desktop_entry(path: Path) -> dict[str, str] | None:
    parser = _DesktopParser(interpolation=None, strict=False, delimiters=("=",))
    try:
        with path.open(encoding="utf-8") as stream:
            parser.read_file(stream)
        entry = dict(parser["Desktop Entry"])
    except (OSError, UnicodeError, configparser.Error, KeyError):
        return None
    if entry.get("Type") != "Application" or entry.get("Hidden", "").lower() == "true":
        return None
    return entry


def _desktop_packages(path: Path) -> list[PackageMatch]:
    try:
        resolved = path.resolve(strict=True)
    except OSError:
        resolved = path
    paths = (path,) if path == resolved else (path, resolved)
    matches: list[PackageMatch] = []
    package = _dpkg_owner(paths)
    if package:
        matches.append(PackageMatch("dpkg", package, "sudo apt remove " + shlex.quote(package), evidence="dpkg path record"))
    package = _brew_owner(paths)
    if package:
        matches.append(PackageMatch("brew", package, "brew uninstall " + shlex.quote(package), evidence="Homebrew file record"))
    if path.parent == Path("/var/lib/snapd/desktop/applications"):
        candidates = [name for name in _snap_packages() if path.name.startswith(name + "_")]
        if candidates:
            package = max(candidates, key=len)
            matches.append(PackageMatch("snap", package, "sudo snap remove " + shlex.quote(package), evidence="Snap desktop export"))
    if tuple(path.parent.parts[-3:]) == ("exports", "share", "applications"):
        package = path.stem
        if package in _flatpak_apps():
            matches.append(PackageMatch("flatpak", package, "flatpak uninstall " + shlex.quote(package), evidence="Flatpak desktop export"))
    return matches


def search_desktop(query: str) -> list[DesktopMatch]:
    direct = "/" in query and query.casefold().endswith(".desktop")
    filename_search = query.casefold().endswith(".desktop")
    files = [Path(os.path.expandvars(os.path.expanduser(query))).absolute()] if direct else _desktop_files()
    matches: list[DesktopMatch] = []
    for path in files:
        if filename_search and not direct and query.casefold() not in path.name.casefold():
            continue
        entry = _desktop_entry(path)
        if entry is None:
            continue
        names = [value for key, value in entry.items() if key == "Name" or (key.startswith("Name[") and key.endswith("]"))]
        matched = [name for name in names if query.casefold() in name.casefold()]
        if not filename_search and not matched:
            continue
        matches.append(DesktopMatch(entry.get("Name", ""), matched, str(path), _desktop_packages(path)))
    return matches


def _desktop_result(query: str, matches: list[DesktopMatch]) -> dict[str, object]:
    return {"query": query, "note": None if matches else "no desktop application matched", "matches": [
        {"name": m.name, "matched_names": m.matched_names, "file": m.file,
         "packages": [{"manager": p.manager, "package": p.package, "uninstall": p.uninstall, "evidence": p.evidence} for p in m.packages]}
        for m in matches
    ]}


def _format_desktop(query: str, matches: list[DesktopMatch], no_limit: bool) -> str:
    lines = [query]
    if not matches:
        lines.append("  note:       no desktop application matched")
    for match in matches if no_limit else matches[:20]:
        lines.append("  application: " + (match.name or Path(match.file).name))
        for name in match.matched_names:
            if name != match.name:
                lines.append("    matched:  " + name)
        lines.append("    file:     " + match.file)
        if match.packages:
            lines.extend(_format_matches(match.packages, True, path_records=True))
        else:
            lines.append("    package:  unknown")
    if not no_limit and len(matches) > 20:
        lines.append("  ... {} more matches; use --no-limit to display all matches".format(len(matches) - 20))
    return "\n".join(lines)


def search_fonts(query: str) -> list[FontMatch] | None:
    """Find installed Fontconfig faces and identify their file owners."""
    output = _run(["fc-list", "--format", "%{family}\t%{style}\t%{fullname}\t%{file}\n"])
    if output is None:
        return None
    needle = _normalize_name(query)
    matches = []
    owners: dict[str, list[PackageMatch]] = {}
    seen: set[tuple[str, str, str, str]] = set()
    for line in output.splitlines():
        fields = line.split("\t", 3)
        if len(fields) != 4 or not fields[3]:
            continue
        family, style, fullname, filename = fields
        if not any(needle in _normalize_name(name) for name in (family, fullname)):
            continue
        key = (family, style, fullname, filename)
        if key in seen:
            continue
        seen.add(key)
        if filename not in owners:
            path = Path(filename)
            try:
                realpath = path.resolve(strict=True)
            except OSError:
                realpath = path
            paths = (path,) if path == realpath else (path, realpath)
            packages = []
            package = _dpkg_owner(paths)
            if package:
                packages.append(PackageMatch("dpkg", package, "sudo apt remove " + shlex.quote(package), evidence="dpkg path record"))
            package = _brew_owner(paths)
            if package:
                packages.append(PackageMatch("brew", package, "brew uninstall " + shlex.quote(package), evidence="Homebrew file record"))
            owners[filename] = packages
        matches.append(FontMatch(family, style, fullname, filename, owners[filename]))
    return sorted(matches, key=lambda item: (item.family.casefold(), item.style.casefold(), item.file))


def _font_result(query: str, matches: list[FontMatch] | None) -> dict[str, object]:
    return {
        "font": query,
        "note": "Fontconfig fc-list is unavailable" if matches is None else ("no installed font matched" if not matches else None),
        "matches": [
            {"family": m.family, "style": m.style, "fullname": m.fullname, "file": m.file,
             "packages": [{"manager": p.manager, "package": p.package, "uninstall": p.uninstall, "evidence": p.evidence} for p in m.packages]}
            for m in matches or []
        ],
    }


def _format_fonts(query: str, matches: list[FontMatch] | None, no_limit: bool) -> str:
    lines = [query]
    if matches is None:
        lines.append("  note:       Fontconfig fc-list is unavailable")
    elif not matches:
        lines.append("  note:       no installed font matched")
    else:
        for match in matches if no_limit else matches[:20]:
            lines.append("  font:       " + (match.fullname or match.family))
            lines.append("    family:   " + match.family)
            lines.append("    style:    " + match.style)
            lines.append("    file:     " + match.file)
            if match.packages:
                lines.extend(_format_matches(match.packages, True, path_records=True))
            else:
                lines.append("    package:  unknown")
        if not no_limit and len(matches) > 20:
            lines.append("  ... {} more matches; use --no-limit to display all matches".format(len(matches) - 20))
    return "\n".join(lines)


def _run(args: Sequence[str]) -> str | None:
    """Run a package-manager query without invoking a shell."""
    try:
        result = subprocess.run(
            args,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def _resolve(command: str) -> Path | None:
    """Resolve a command name or an explicitly supplied path."""
    expanded = Path(os.path.expandvars(os.path.expanduser(command)))
    if expanded.is_absolute() or "/" in command:
        if expanded.exists() and expanded.is_file():
            return expanded.absolute()
        return None
    found = shutil.which(command)
    return Path(found).absolute() if found else None


def _dpkg_owner(paths: Sequence[Path]) -> str | None:
    for path in paths:
        output = _run(["dpkg-query", "-S", str(path)])
        if not output:
            continue
        for line in output.splitlines():
            # dpkg-query emits one "package: path" record per owner.
            package, separator, owned_path = line.partition(": ")
            if separator and owned_path == str(path):
                return package[:-4] if package.endswith(":any") else package
    return None


@lru_cache(maxsize=1)
def _snap_packages() -> set[str]:
    output = _run(["snap", "list"])
    if not output:
        return set()
    rows = output.splitlines()[1:]
    return {row.split()[0] for row in rows if row.split()}


def _snap_owner(executable: Path) -> str | None:
    # Snap command aliases live in /snap/bin even when their target resolves
    # to /usr/bin/snap, so inspect the original path before its realpath.
    if executable.parent != Path("/snap/bin"):
        return None
    candidate = executable.name
    packages = _snap_packages()
    if candidate in packages:
        return candidate
    # Additional applications from a snap are exposed as <snap>.<app>.
    matches = [name for name in packages if candidate.startswith(name + ".")]
    return max(matches, key=len) if matches else None


@lru_cache(maxsize=1)
def _flatpak_apps() -> set[str]:
    output = _run(["flatpak", "list", "--app", "--columns=application"])
    return set(output.splitlines()) if output else set()


@lru_cache(maxsize=1)
def _pipx_tools() -> list[tuple[str, set[str]]]:
    output = _run(["pipx", "list", "--output", "json"])
    if not output:
        return []
    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        return []

    tools: list[tuple[str, set[str]]] = []
    venvs = data.get("venvs", {})
    if not isinstance(venvs, dict):
        return tools
    for environment, record in venvs.items():
        if not isinstance(record, dict):
            continue
        metadata = record.get("metadata", record)
        if not isinstance(metadata, dict):
            continue
        main_package = metadata.get("main_package", {})
        if not isinstance(main_package, dict):
            continue
        package = main_package.get("package") or environment
        apps = set()
        for app in main_package.get("apps", []):
            if isinstance(app, str):
                apps.add(Path(app).name)
        dependency_apps = main_package.get("apps_of_dependencies", {})
        if isinstance(dependency_apps, dict):
            for app_list in dependency_apps.values():
                if isinstance(app_list, list):
                    apps.update(Path(app).name for app in app_list if isinstance(app, str))
        tools.append((str(package), apps))
    return tools


@lru_cache(maxsize=1)
def _uv_tools() -> list[tuple[str, set[str]]]:
    output = _run(["uv", "tool", "list", "--show-paths"])
    if not output:
        return []

    tools: list[tuple[str, set[str]]] = []
    current_package: str | None = None
    current_apps: set[str] = set()
    for line in output.splitlines():
        header = re.match(r"^(\S+)\s+v\S+", line)
        if header:
            if current_package is not None:
                tools.append((current_package, current_apps))
            current_package = header.group(1)
            current_apps = set()
            continue
        app = re.match(r"^\s+-\s+(\S+)\s*$", line)
        if app and "/" not in app.group(1):
            current_apps.add(Path(app.group(1)).name)
    if current_package is not None:
        tools.append((current_package, current_apps))
    return tools


@lru_cache(maxsize=1)
def _cargo_tools() -> list[tuple[str, set[str]]]:
    output = _run(["cargo", "install", "--list"])
    if not output:
        return []

    tools: list[tuple[str, set[str]]] = []
    current_package: str | None = None
    current_bins: set[str] = set()
    for line in output.splitlines():
        header = re.match(r"^(.+?)\s+v[^\s:]+:", line)
        if header:
            if current_package is not None:
                tools.append((current_package, current_bins))
            current_package = header.group(1)
            current_bins = set()
            continue
        binary = re.match(r"^\s+([^\s]+)\s*$", line)
        if binary:
            current_bins.add(binary.group(1))
    if current_package is not None:
        tools.append((current_package, current_bins))
    return tools


@lru_cache(maxsize=1)
def _brew_packages() -> list[tuple[str, str]]:
    packages: list[tuple[str, str]] = []
    for kind, args in (
        ("formula", ["brew", "list", "--formula"]),
        ("cask", ["brew", "list", "--cask"]),
    ):
        output = _run(args)
        if output:
            packages.extend((kind, line.strip()) for line in output.splitlines() if line.strip())
    return packages


@lru_cache(maxsize=1)
def _brew_owner(paths: tuple[Path, ...]) -> str | None:
    output = _run(["brew", "list", "--formula", "--verbose"])
    if not output:
        return None
    expected = {path.resolve() for path in paths}
    for line in output.splitlines():
        candidate = Path(line.strip())
        if not candidate.is_absolute():
            continue
        try:
            if candidate.resolve() in expected:
                parts = candidate.parts
                if "Cellar" in parts:
                    index = parts.index("Cellar")
                    if index + 1 < len(parts):
                        return parts[index + 1]
        except OSError:
            continue
    return None


def _pipx_owner(executable: Path, realpath: Path) -> str | None:
    expected_root = Path(
        os.environ.get("PIPX_HOME", Path.home() / ".local/share/pipx")
    ).expanduser()
    try:
        is_pipx_path = realpath.is_relative_to(expected_root)
    except (OSError, ValueError):
        is_pipx_path = False
    if not is_pipx_path:
        return None
    for package, apps in _pipx_tools():
        if executable.name in apps:
            return package
    return None


def _uv_owner(executable: Path, realpath: Path) -> str | None:
    expected_root = Path(
        os.environ.get("UV_TOOL_DIR", Path.home() / ".local/share/uv/tools")
    ).expanduser()
    try:
        is_uv_path = realpath.is_relative_to(expected_root)
    except (OSError, ValueError):
        is_uv_path = False
    if not is_uv_path:
        return None
    for package, apps in _uv_tools():
        if executable.name in apps:
            return package
    return None


def _cargo_owner(executable: Path, realpath: Path) -> str | None:
    cargo_home = Path(os.environ.get("CARGO_HOME", Path.home() / ".cargo")).expanduser()
    try:
        is_cargo_path = executable.is_relative_to(cargo_home / "bin") or realpath.is_relative_to(
            cargo_home / "bin"
        )
    except (OSError, ValueError):
        is_cargo_path = False
    if not is_cargo_path:
        return None
    for package, binaries in _cargo_tools():
        if executable.name in binaries:
            return package
    return None


def _search_installed_packages(query: str) -> list[PackageMatch]:
    """Find installed packages whose identifier or display name contains query."""
    needle = _normalize_name(query)
    matches: list[PackageMatch] = []

    output = _run(
        ["dpkg-query", "-W", "-f=${binary:Package}\t${Version}\t${Section}\n"]
    )
    if output:
        for line in output.splitlines():
            fields = line.split("\t", 2)
            if len(fields) >= 2 and needle in _normalize_name(fields[0]):
                package = fields[0]
                section = fields[2].strip() if len(fields) > 2 else ""
                matches.append(
                    PackageMatch(
                        "dpkg",
                        package,
                        "sudo apt remove " + shlex.quote(package),
                        section=section or None,
                    )
                )

    for package in sorted(_snap_packages()):
        if needle in _normalize_name(package):
            matches.append(
                PackageMatch(
                    "snap",
                    package,
                    "sudo snap remove " + shlex.quote(package),
                )
            )

    flatpak_output = _run(
        ["flatpak", "list", "--app", "--columns=application,name"]
    )
    if flatpak_output:
        for line in flatpak_output.splitlines():
            fields = line.split("\t", 1)
            if len(fields) == 1:
                fields = line.split(None, 1)
            app_id = fields[0].strip()
            display_name = fields[1].strip() if len(fields) > 1 else ""
            if needle in _normalize_name(app_id) or needle in _normalize_name(display_name):
                matches.append(
                    PackageMatch(
                        "flatpak",
                        app_id,
                        "flatpak uninstall " + shlex.quote(app_id),
                        display_name or None,
                    )
                )

    for manager, tools, uninstall_prefix in (
        ("pipx", _pipx_tools(), "pipx uninstall "),
        ("uv", _uv_tools(), "uv tool uninstall "),
        ("cargo", _cargo_tools(), "cargo uninstall "),
    ):
        for package, commands in tools:
            matching_command = next(
                (name for name in sorted(commands) if needle in _normalize_name(name)),
                None,
            )
            if needle in _normalize_name(package) or matching_command:
                matches.append(
                    PackageMatch(
                        manager,
                        package,
                        uninstall_prefix + shlex.quote(package),
                        display_name=matching_command,
                    )
                )

    for kind, package in _brew_packages():
        if needle in _normalize_name(package):
            uninstall = "brew uninstall "
            if kind == "cask":
                uninstall += "--cask "
            matches.append(
                PackageMatch(
                    "brew",
                    package,
                    uninstall + shlex.quote(package),
                )
            )

    return matches


def _normalize_name(value: str) -> str:
    """Normalize package names for case- and punctuation-insensitive matching."""
    return "".join(character for character in value.casefold() if character.isalnum())


def _flatpak_owner(executable: Path) -> str | None:
    # Flatpak exports launchers under exports/bin/<application-id>. A normal
    # command provided inside an app is not exposed on the host PATH.
    parts = executable.parts
    try:
        marker = parts.index("exports")
    except ValueError:
        return None
    if marker + 2 >= len(parts) or parts[marker + 1] != "bin":
        return None
    candidate = executable.name
    return candidate if candidate in _flatpak_apps() else None


def inspect(command: str) -> Result:
    executable = _resolve(command)
    if executable is None:
        return Result(command, None, None, "unknown", note="command not found")

    try:
        realpath = executable.resolve(strict=True)
    except OSError:
        realpath = executable

    is_manager_launcher = executable.parent == Path("/snap/bin") or (
        "exports" in executable.parts and "bin" in executable.parts
    )
    paths = (
        (executable,)
        if is_manager_launcher or executable == realpath
        else (executable, realpath)
    )

    direct_matches: list[PackageMatch] = []
    package = _dpkg_owner(paths)
    if package:
        direct_matches.append(
            PackageMatch(
                "dpkg",
                package,
                "sudo apt remove " + shlex.quote(package),
                evidence="dpkg path record",
            )
        )

    package = _snap_owner(executable)
    if package:
        direct_matches.append(
            PackageMatch(
                "snap",
                package,
                "sudo snap remove " + shlex.quote(package),
                evidence="snap command path",
            )
        )

    package = _flatpak_owner(executable)
    if package:
        direct_matches.append(
            PackageMatch(
                "flatpak",
                package,
                "flatpak uninstall " + shlex.quote(package),
                evidence="Flatpak export path",
            )
        )

    package = _pipx_owner(executable, realpath)
    if package:
        direct_matches.append(
            PackageMatch(
                "pipx",
                package,
                "pipx uninstall " + shlex.quote(package),
                evidence="pipx exposed app",
            )
        )

    package = _uv_owner(executable, realpath)
    if package:
        direct_matches.append(
            PackageMatch(
                "uv",
                package,
                "uv tool uninstall " + shlex.quote(package),
                evidence="uv exposed app",
            )
        )

    package = _cargo_owner(executable, realpath)
    if package:
        direct_matches.append(
            PackageMatch(
                "cargo",
                package,
                "cargo uninstall " + shlex.quote(package),
                evidence="Cargo installed binary",
            )
        )

    package = _brew_owner(paths)
    if package:
        direct_matches.append(
            PackageMatch(
                "brew",
                package,
                "brew uninstall " + shlex.quote(package),
                evidence="Homebrew file record",
            )
        )

    matches = direct_matches
    matches.sort(key=lambda match: (match.manager, match.package))
    note = None
    if not matches:
        note = (
            "no supported package manager claims this executable; "
            "it may have been installed manually or by another installer"
        )
    path_setup = find_path_setup(executable.parent)
    return Result(
        command,
        str(executable),
        str(realpath),
        matches[0].manager if matches else "unknown",
        matches[0].package if matches else None,
        matches[0].uninstall if matches else None,
        note,
        matches,
        path_setup,
    )


def _format_matches(
    matches: Sequence[PackageMatch],
    no_limit: bool,
    *,
    path_records: bool = False,
) -> list[str]:
    visible = list(matches)
    if not path_records:
        visible.sort(key=lambda match: (match.manager, match.package.casefold()))
    shown = visible if no_limit else visible[:20]
    lines = []
    for match in shown:
        evidence = " ({})".format(match.evidence) if path_records else ""
        description = "    - {}{}: {}".format(match.manager, evidence, match.package)
        if match.section:
            description += " [section: {}]".format(match.section)
        if match.display_name and match.display_name.casefold() != match.package.casefold():
            description += " ({})".format(match.display_name)
        lines.append(description)
        lines.append("        uninstall: " + match.uninstall)
    remaining = len(visible) - len(shown)
    if remaining:
        lines.append(
            "  ... {} more matches; use --no-limit to display all selected matches".format(remaining)
        )
    return lines


def _json_result(
    command: str,
    result: Result,
    matches: Sequence[PackageMatch],
) -> dict[str, object]:
    visible = list(matches)
    visible.sort(key=lambda match: (match.evidence == "name match", match.manager, match.package))
    primary = visible[0] if visible else None
    note = result.note

    return {
        "command": command,
        "executable": result.executable,
        "realpath": result.realpath,
        "manager": primary.manager if primary else "unknown",
        "package": primary.package if primary else None,
        "uninstall": primary.uninstall if primary else None,
        "note": note,
        "matches": [
            {
                "manager": match.manager,
                "package": match.package,
                "uninstall": match.uninstall,
                "evidence": match.evidence,
                "display_name": match.display_name,
                "section": match.section,
            }
            for match in visible
        ],
        "path_setup": [
            {"file": item.file, "line": item.line, "entry": item.entry}
            for item in result.path_setup
        ],
    }


def _format(result: Result, no_limit: bool = False) -> str:
    lines = [result.command]
    if result.executable is None:
        lines.append("  manager:    unknown")
        lines.append("  note:       " + (result.note or "command not found"))
        return "\n".join(lines)
    lines.extend(
        [
            "  PATH resolution:",
            "    executable: " + result.executable,
            "    realpath:   " + (result.realpath or result.executable),
        ]
    )
    if result.matches:
        lines.append("  package manager records:")
        lines.extend(_format_matches(result.matches, no_limit, path_records=True))
    else:
        lines.append("  manager:    unknown")
    if result.note:
        lines.append("  note:       " + result.note)
    if result.path_setup:
        lines.append("  PATH entry sources:")
        for item in result.path_setup:
            lines.append(f"    {item.file}:{item.line}  {item.entry}")
        lines.append(
            "  note:       these sources show where PATH is recorded, not how "
            "the executable was installed"
        )
    else:
        lines.append(
            "  PATH entry sources: none found in checked shell config files or "
            "recognized Fish universal paths"
        )
    return "\n".join(lines)


def _format_search(
    command: str,
    matches: Sequence[PackageMatch],
    no_limit: bool = False,
) -> str:
    lines = [command]
    if not matches:
        lines.extend(
            [
                "  manager:    unknown",
                "  note:       no installed dpkg, snap, flatpak, pipx, uv, Cargo, or Homebrew package matched",
            ]
        )
    else:
        lines.append("  package matches:")
        lines.extend(_format_matches(matches, no_limit))
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="pkgwho",
        description=(
            "Search commands, installed packages, fonts, and desktop applications."
        ),
    )
    parser.add_argument("--version", action="version", version="%(prog)s " + __version__)
    parser.add_argument(
        "--type", choices=("command", "package", "font", "desktop", "all"), default="all",
        help="what to search (default: all)",
    )
    parser.add_argument(
        "commands", nargs="+", metavar="QUERY",
        help="command, package, font, or desktop application search text",
    )
    parser.add_argument(
        "--no-limit",
        action="store_true",
        help="display every selected match without the default 20-match limit",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="output all results as a JSON array",
    )
    parser.add_argument(
        "-o",
        "--output",
        metavar="FILE",
        dest="output_file",
        help="write output to FILE instead of standard output",
    )
    args = parser.parse_args(argv)
    output: list[Any] = []
    unresolved = False
    for command in args.commands:
        results: dict[str, Any] = {}
        found = False
        if args.type in ("command", "all"):
            result = inspect(command)
            found = result.executable is not None
            results["command"] = (
                _json_result(command, result, result.matches)
                if args.json else _format(result, args.no_limit)
            )
        if args.type in ("package", "all"):
            matches = _search_installed_packages(command)
            found = found or bool(matches)
            results["package"] = (
                {"query": command, "matches": [
                    {"manager": m.manager, "package": m.package,
                     "uninstall": m.uninstall, "evidence": m.evidence,
                     "display_name": m.display_name, "section": m.section}
                    for m in matches
                ], "note": None if matches else "no installed package matched"}
                if args.json else _format_search(command, matches, args.no_limit)
            )
        if args.type in ("font", "all"):
            font_matches = search_fonts(command)
            found = found or bool(font_matches)
            results["font"] = (
                _font_result(command, font_matches)
                if args.json else _format_fonts(command, font_matches, args.no_limit)
            )
        if args.type in ("desktop", "all"):
            desktop_matches = search_desktop(command)
            found = found or bool(desktop_matches)
            results["desktop"] = (
                _desktop_result(command, desktop_matches)
                if args.json else _format_desktop(command, desktop_matches, args.no_limit)
            )
        unresolved = unresolved or not found
        if args.type == "all":
            if args.json:
                output.append({"query": command, **results})
            else:
                output.append("\n\n".join(
                    name.capitalize() + " search:\n" + results[name]
                    for name in ("command", "package", "font", "desktop")
                ))
        else:
            output.append(results[args.type])
    if args.json:
        rendered_output = json.dumps(output, ensure_ascii=False, indent=2)
    else:
        rendered_output = "\n\n".join(output)
    if args.output_file is not None:
        try:
            Path(args.output_file).write_text(rendered_output + "\n", encoding="utf-8")
        except OSError as exc:
            parser.error("could not write output file {!r}: {}".format(args.output_file, exc))
    else:
        print(rendered_output)
    return 1 if unresolved else 0


if __name__ == "__main__":
    sys.exit(main())
