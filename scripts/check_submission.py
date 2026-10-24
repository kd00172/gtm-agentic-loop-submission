#!/usr/bin/env python3
"""Check an exact submission allowlist, staged blobs, or reachable Git history."""
import argparse
import json
from pathlib import Path, PurePosixPath
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
PATTERNS = {
    "credential_pattern": r"\b(?:sk-(?:proj-)?[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|xox[baprs]-[A-Za-z0-9-]{15,})\b",
    "personal_absolute_path": r"(?:/Users/|/home/)[A-Za-z0-9_.-]+/",
    "email_address": r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
    "private_key": r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
}


def git(*args):
    return subprocess.check_output(["git", "-C", str(ROOT), *args])


def manifest():
    names = (ROOT / "submission-files.txt").read_text().splitlines()
    if len(names) != len(set(names)):
        raise ValueError("duplicate_manifest_path")
    for name in names:
        path = PurePosixPath(name)
        if not name or path.is_absolute() or ".." in path.parts or ".git" in path.parts:
            raise ValueError("invalid_manifest_path")
    return set(names)


def inspect_text(data, deny_terms=()):
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return ["binary_content_refused"]
    errors = [key for key, pattern in PATTERNS.items() if re.search(pattern, text)]
    if any(term.casefold() in text.casefold() for term in deny_terms):
        errors.append("operator_denied_term")
    for url in re.findall(r"https?://[A-Za-z0-9][^\s)\]>\"']+", text):
        if not re.match(r"https://(?:learn\.chatgpt\.com|developers\.openai\.com)/", url):
            errors.append("unapproved_external_url")
    return sorted(set(errors))


def git_tree(revision=None):
    if revision:
        records = git("ls-tree", "-rz", revision).split(b"\0")
        return [(row.split(b"\t", 1)[1].decode(), row.split(b" ", 1)[0], row.split(b"\t", 1)[0].split()[-1]) for row in records if row]
    records = git("ls-files", "--stage", "-z").split(b"\0")
    return [(row.split(b"\t", 1)[1].decode(), row.split(b" ", 1)[0], row.split(b" ", 2)[1]) for row in records if row]


def check(staged=False, history=False, deny_terms=()):
    allowed = manifest()
    errors = []
    for name in sorted(allowed):
        path = ROOT / name
        if path.is_symlink() or any(parent.is_symlink() for parent in path.parents if parent != ROOT.parent):
            errors.append({"file": name, "rule": "symlink_refused"})
        elif not path.is_file():
            errors.append({"file": name, "rule": "missing_manifest_file"})
        else:
            errors.extend({"file": name, "rule": rule} for rule in inspect_text(path.read_bytes(), deny_terms))
    trees = []
    if staged:
        trees.append(git_tree())
    elif history:
        trees += [git_tree(commit) for commit in git("rev-list", "--all").decode().splitlines()]
    else:
        trees.append(git_tree())
    checked_blobs = set()
    for tree in trees:
        for name, mode, oid in tree:
            if name not in allowed:
                errors.append({"rule": "unlisted_git_path"})
                continue
            if mode not in (b"100644", b"100755"):
                errors.append({"file": name, "rule": "non_regular_git_object"})
                continue
            if oid not in checked_blobs:
                errors.extend({"file": name, "rule": rule} for rule in inspect_text(git("cat-file", "blob", oid.decode()), deny_terms))
                checked_blobs.add(oid)
    return {"status": "passed" if not errors else "failed", "allowlisted_files": len(allowed),
            "checked_git_blobs": len(checked_blobs), "errors": errors}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--staged", action="store_true")
    group.add_argument("--history", action="store_true")
    parser.add_argument("--deny-file", type=Path, help="External file with private terms; matches are never printed")
    args = parser.parse_args()
    terms = [x.strip() for x in args.deny_file.read_text().splitlines() if x.strip()] if args.deny_file else []
    result = check(args.staged, args.history, terms)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
