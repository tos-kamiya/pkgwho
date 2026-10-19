# SPDX-FileCopyrightText: 2026-present Toshihiro Kamiya <kamiya@mbj.nifty.com>
#
# SPDX-License-Identifier: MIT
"""Command line interface for identifying package ownership."""

from __future__ import annotations

import argparse
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
from typing import Sequence

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

    tools = []
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
    packages = []
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


def _show_dpkg_match_by_default(match: PackageMatch) -> bool:
    if match.manager != "dpkg":
        return True

    section = (match.section or "").casefold().rsplit("/", 1)[-1]
    if section in {
        "admin",
        "comm",
        "editors",
        "games",
        "graphics",
        "interpreters",
        "net",
        "science",
        "shells",
        "sound",
        "utils",
        "video",
        "web",
        "x11",
    }:
        return True

    if section in {
        "debug",
        "doc",
        "documentation",
        "kernel",
        "libdevel",
        "libs",
        "misc",
        "oldlibs",
    }:
        return False

    # Some third-party repositories use a vendor name as the Section (for
    # example, "NVIDIA"). Allow recognizable command/tool package names there,
    # while leaving kernels, firmware, libraries, documentation, and meta
    # packages hidden by default.
    package = match.package.split(":", 1)[0].casefold()
    tool_tokens = {
        "bin",
        "cli",
        "gdb",
        "settings",
        "smi",
        "toolkit",
        "tools",
        "utils",
    }
    if tool_tokens.intersection(package.replace("_", "-").split("-")):
        return True

    return False


def _is_library_package(match: PackageMatch) -> bool:
    if match.manager != "dpkg":
        return False

    section = (match.section or "").casefold().rsplit("/", 1)[-1]
    if section in {"libdevel", "libs", "oldlibs"}:
        return True

    if section in {
        "admin",
        "editors",
        "games",
        "graphics",
        "interpreters",
        "net",
        "science",
        "shells",
        "sound",
        "utils",
        "video",
        "web",
        "x11",
    }:
        return False

    package = match.package.split(":", 1)[0].casefold()
    return package.startswith("lib")


def _show_match(
    match: PackageMatch,
    apps_only: bool,
    *,
    direct_path_match: bool = False,
) -> bool:
    if not apps_only:
        return True
    if _is_library_package(match):
        return False
    return direct_path_match or _show_dpkg_match_by_default(match)


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

    # Also search other managers for the command or the package name that owns
    # its active executable. This can reveal parallel installs such as a deb
    # and a Flatpak with differently punctuated names (draw.io / drawio).
    search_terms = {executable.name}
    search_terms.update(match.package for match in direct_matches)
    direct_managers = {match.manager for match in direct_matches}
    matches = list(direct_matches)
    seen = {(match.manager, match.package) for match in matches}
    for term in search_terms:
        normalized_term = _normalize_name(term)
        if not normalized_term:
            continue
        for candidate in _search_installed_packages(term):
            identity = (candidate.manager, candidate.package)
            if identity in seen or candidate.manager in direct_managers:
                continue
            candidate_names = [candidate.package, candidate.display_name or ""]
            if not any(
                normalized_term == _normalize_name(name)
                or (
                    len(normalized_term) >= 4
                    and normalized_term in _normalize_name(name)
                )
                for name in candidate_names
            ):
                continue
            seen.add(identity)
            matches.append(candidate)

    matches.sort(
        key=lambda match: (match.evidence == "name match", match.manager, match.package)
    )
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
    apps_only: bool,
    no_limit: bool,
    *,
    path_records: bool = False,
) -> list[str]:
    visible = _visible_matches(matches, apps_only, path_records=path_records)
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
    filtered = len(matches) - len(visible)
    if filtered:
        lines.append(
            "  note: {} package match(es) excluded by --apps-only; "
            "use --apps-only false to include them".format(filtered)
        )
    return lines


def _visible_matches(
    matches: Sequence[PackageMatch],
    apps_only: bool,
    *,
    path_records: bool = False,
) -> list[PackageMatch]:
    return [
        match for match in matches
        if _show_match(
            match,
            apps_only,
            direct_path_match=path_records and match.evidence != "name match",
        )
    ]


def _json_result(
    command: str,
    result: Result,
    matches: Sequence[PackageMatch],
    apps_only: bool,
) -> dict[str, object]:
    path_records = result.executable is not None
    visible = _visible_matches(matches, apps_only, path_records=path_records)
    visible.sort(key=lambda match: (match.evidence == "name match", match.manager, match.package))
    primary = visible[0] if visible else None
    note = result.note
    if result.executable is None and not matches:
        note = "no installed package matched"

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
        "filtered_match_count": len(matches) - len(visible),
        "path_setup": [
            {"file": item.file, "line": item.line, "entry": item.entry}
            for item in result.path_setup
        ],
    }


def _format(result: Result, apps_only: bool = True, no_limit: bool = False) -> str:
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
        lines.extend(_format_matches(result.matches, apps_only, no_limit, path_records=True))
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
    apps_only: bool = True,
    no_limit: bool = False,
) -> str:
    lines = [command, "  command:    not found in PATH"]
    if not matches:
        lines.extend(
            [
                "  manager:    unknown",
                "  note:       no installed dpkg, snap, flatpak, pipx, uv, Cargo, or Homebrew package matched",
            ]
        )
    else:
        lines.append("  package matches:")
        lines.extend(_format_matches(matches, apps_only, no_limit))
    return "\n".join(lines)


def _parse_bool(value: str) -> bool:
    """Parse a boolean command-line value."""
    normalized = value.casefold()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    raise argparse.ArgumentTypeError("expected 'true' or 'false'")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="pkgwho",
        description=(
            "Find which supported package manager installed a command, or search "
            "installed packages when it is not in PATH."
        ),
    )
    parser.add_argument("--version", action="version", version="%(prog)s " + __version__)
    parser.add_argument(
        "commands", nargs="+", metavar="COMMAND",
        help="command name, executable path, or package search text",
    )
    parser.add_argument(
        "--apps-only",
        type=_parse_bool,
        metavar="BOOL",
        default=True,
        help="show application/tool candidates only (true/false; default: true)",
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
    output = []
    unresolved = False
    for command in args.commands:
        result = inspect(command)
        if result.executable is None:
            matches = _search_installed_packages(command)
            unresolved = unresolved or not matches
            if args.json:
                output.append(_json_result(command, result, matches, args.apps_only))
            else:
                output.append(_format_search(command, matches, args.apps_only, args.no_limit))
        else:
            if args.json:
                output.append(_json_result(command, result, result.matches, args.apps_only))
            else:
                output.append(_format(result, args.apps_only, args.no_limit))
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
