# SPDX-FileCopyrightText: 2026-present Toshihiro Kamiya <kamiya@mbj.nifty.com>
#
# SPDX-License-Identifier: MIT
"""Command line interface for identifying package ownership."""

from __future__ import annotations

import argparse
import os
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from pkgwho.__about__ import __version__


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


def _flatpak_apps() -> set[str]:
    output = _run(["flatpak", "list", "--app", "--columns=application"])
    return set(output.splitlines()) if output else set()


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
    return Result(
        command,
        str(executable),
        str(realpath),
        matches[0].manager if matches else "unknown",
        matches[0].package if matches else None,
        matches[0].uninstall if matches else None,
        note,
        matches,
    )


def _format_matches(
    matches: Sequence[PackageMatch],
    apps_only: bool,
    no_limit: bool,
    *,
    path_records: bool = False,
) -> list[str]:
    visible = [
        match for match in matches
        if _show_match(
            match,
            apps_only,
            direct_path_match=path_records and match.evidence != "name match",
        )
    ]
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
                "  note:       no installed dpkg, snap, or flatpak package matched",
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
    args = parser.parse_args(argv)
    output = []
    unresolved = False
    for command in args.commands:
        result = inspect(command)
        if result.executable is None:
            matches = _search_installed_packages(command)
            unresolved = unresolved or not matches
            output.append(_format_search(command, matches, args.apps_only, args.no_limit))
        else:
            output.append(_format(result, args.apps_only, args.no_limit))
    print("\n\n".join(output))
    return 1 if unresolved else 0


if __name__ == "__main__":
    sys.exit(main())
