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
        ["dpkg-query", "-W", "-f=${binary:Package}\t${Version}\n"]
    )
    if output:
        for line in output.splitlines():
            package, separator, _version = line.partition("\t")
            if separator and needle in _normalize_name(package):
                matches.append(
                    PackageMatch(
                        "dpkg",
                        package,
                        "sudo apt remove " + shlex.quote(package),
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


def _format(result: Result) -> str:
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
        for match in result.matches:
            description = "    - {} ({}): {}".format(
                match.manager, match.evidence, match.package
            )
            if match.display_name and _normalize_name(match.display_name) != _normalize_name(match.package):
                description += " ({})".format(match.display_name)
            lines.append(description)
            lines.append("        uninstall: " + match.uninstall)
    else:
        lines.append("  manager:    unknown")
    if result.note:
        lines.append("  note:       " + result.note)
    return "\n".join(lines)


def _format_search(command: str, matches: Sequence[PackageMatch]) -> str:
    lines = [command, "  command:    not found in PATH"]
    if not matches:
        lines.extend(
            [
                "  manager:    unknown",
                "  note:       no installed dpkg, snap, or flatpak package matched",
            ]
        )
        return "\n".join(lines)

    lines.append("  package name matches:")
    shown = matches[:20]
    for match in shown:
        description = "  - {}: {}".format(match.manager, match.package)
        if match.display_name and match.display_name.casefold() != match.package.casefold():
            description += " ({})".format(match.display_name)
        lines.append(description)
        lines.append("      uninstall: " + match.uninstall)
    if len(matches) > len(shown):
        lines.append("  ... and {} more matches".format(len(matches) - len(shown)))
    return "\n".join(lines)


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
        "commands",
        nargs="+",
        metavar="COMMAND",
        help="command name, executable path, or package search text",
    )
    args = parser.parse_args(argv)

    output = []
    unresolved = False
    for command in args.commands:
        result = inspect(command)
        if result.executable is None:
            matches = _search_installed_packages(command)
            unresolved = unresolved or not matches
            output.append(_format_search(command, matches))
        else:
            output.append(_format(result))
    print("\n\n".join(output))
    return 1 if unresolved else 0


if __name__ == "__main__":
    sys.exit(main())
