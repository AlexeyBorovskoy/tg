#!/usr/bin/env python3
"""
gitlab_push.py — Пуш дайджестов и сводных документов в GitLab (gitlab.ripas.ru)
"""

import logging
import subprocess
from pathlib import Path
from typing import List

logger = logging.getLogger(__name__)


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

    env = dict(subprocess.os.environ)
    if ssh_key_path and Path(ssh_key_path).exists():
        env["GIT_SSH_COMMAND"] = f"ssh -i {ssh_key_path} -o StrictHostKeyChecking=accept-new"

    try:
        for rel in file_paths:
            p = repo_dir / rel
            if p.exists():
                subprocess.run(
                    ["git", "add", rel],
                    cwd=repo_dir,
                    env=env,
                    check=True,
                    capture_output=True,
                )
            else:
                logger.warning("GitLab push: файл не найден %s", p)

        status = subprocess.run(
            ["git", "status", "--short"],
            cwd=repo_dir,
            capture_output=True,
            text=True,
        )
        if not status.stdout.strip():
            logger.info("GitLab push: нечего коммитить (нет изменений)")
            return True

        subprocess.run(
            ["git", "commit", "-m", commit_message],
            cwd=repo_dir,
            env=env,
            check=True,
            capture_output=True,
        )

        remote_branch = f"origin {branch}"
        subprocess.run(
            ["git", "push", "origin", branch],
            cwd=repo_dir,
            env=env,
            check=True,
            capture_output=True,
        )
        logger.info("GitLab push: %s → %s", commit_message, remote_branch)
        return True

    except subprocess.CalledProcessError as e:
        logger.error("GitLab push: ошибка %s", e)
        return False
