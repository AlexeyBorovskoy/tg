#!/usr/bin/env python3
"""
gitlab_push.py — Пуш дайджестов и сводных документов в GitLab (gitlab.ripas.ru)
"""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path
from typing import List

logger = logging.getLogger(__name__)

_DEFAULT_GIT_AUTHOR_NAME = "TG Digest Worker"
_DEFAULT_GIT_AUTHOR_EMAIL = "tg-digest@localhost"


def _run_git(repo_dir: Path, args: list[str], env: dict, check: bool = True) -> subprocess.CompletedProcess:
    """Run git command with good error context."""
    try:
        return subprocess.run(
            ["git", *args],
            cwd=repo_dir,
            env=env,
            check=check,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as e:
        stdout = (e.stdout or "").strip()
        stderr = (e.stderr or "").strip()
        logger.error(
            "GitLab push: git %s failed (code=%s) stdout=%r stderr=%r",
            " ".join(args),
            e.returncode,
            stdout,
            stderr,
        )
        raise


def _make_env(ssh_key_path: str) -> dict:
    env = os.environ.copy()
    if ssh_key_path and Path(ssh_key_path).exists():
        # IdentitiesOnly avoids trying other keys; BatchMode avoids interactive prompts.
        env["GIT_SSH_COMMAND"] = (
            f"ssh -i {ssh_key_path} "
            f"-o IdentitiesOnly=yes "
            f"-o BatchMode=yes "
            f"-o StrictHostKeyChecking=accept-new"
        )
    return env


def _ensure_git_identity(repo_dir: Path, env: dict) -> dict:
    """Ensure git commit doesn't fail due to missing user.name/user.email."""
    name = _run_git(repo_dir, ["config", "--get", "user.name"], env=env, check=False).stdout.strip()
    email = _run_git(repo_dir, ["config", "--get", "user.email"], env=env, check=False).stdout.strip()
    if name and email:
        return env

    new_env = env.copy()
    new_env.setdefault("GIT_AUTHOR_NAME", _DEFAULT_GIT_AUTHOR_NAME)
    new_env.setdefault("GIT_AUTHOR_EMAIL", _DEFAULT_GIT_AUTHOR_EMAIL)
    new_env.setdefault("GIT_COMMITTER_NAME", _DEFAULT_GIT_AUTHOR_NAME)
    new_env.setdefault("GIT_COMMITTER_EMAIL", _DEFAULT_GIT_AUTHOR_EMAIL)
    return new_env


def push_to_gitlab(
    repo_dir: Path,
    file_paths: List[str],
    commit_message: str,
    branch: str = "main",
    ssh_key_path: str = "",
) -> bool:
    """
    Добавляет файлы в git, коммитит и пушит в GitLab.
    Пути в file_paths — относительные от repo_dir (например docs/digests/2026-02-01/...).

    Returns:
        True если пуш успешен, False при ошибке.
    """
    if not file_paths:
        return True

    repo_dir = Path(repo_dir).resolve()
    git_dir = repo_dir / ".git"
    if not git_dir.exists():
        logger.warning("GitLab push: не найден .git в %s", repo_dir)
        return False

    env = _make_env(ssh_key_path)
    env = _ensure_git_identity(repo_dir, env)

    try:
        for rel in file_paths:
            p = repo_dir / rel
            if p.exists():
                # Use "--" to avoid ambiguity and accidental flag injection.
                _run_git(repo_dir, ["add", "--", rel], env=env)
            else:
                logger.warning("GitLab push: файл не найден %s", p)

        status = _run_git(repo_dir, ["status", "--porcelain"], env=env, check=False).stdout.strip()
        if not status:
            logger.info("GitLab push: нечего коммитить (нет изменений)")
            return True

        _run_git(repo_dir, ["commit", "-m", commit_message], env=env)

        # Push current HEAD to the requested branch (works even if we're in detached HEAD).
        _run_git(repo_dir, ["push", "origin", f"HEAD:refs/heads/{branch}"], env=env)
        logger.info("GitLab push: %s → origin/%s", commit_message, branch)
        return True

    except subprocess.CalledProcessError as e:
        # Details already logged in _run_git.
        logger.error("GitLab push: ошибка %s", e)
        return False

