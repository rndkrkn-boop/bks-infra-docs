"""Тесты allow-list гейта для автономного цикла (ci/check-changed-paths.sh).

AUDIT-005 п.5: автономный цикл не может править собственные гейты (ci/,
k8s/, .gitlab-ci.yml, .gitignore) — до 2026-08-06 этот скрипт существовал,
но не вызывался ни одним daily-*.sh, а белый список git add в них сам же
включал ci/ и .gitlab-ci.yml. Тесты проверяют сам гейт: staged, unstaged и
untracked изменения в защищённых путях должны блокировать, изменения в
scripts/ — нет.
"""

import os
import pathlib
import subprocess

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "ci" / "check-changed-paths.sh"

GIT_ENV = {
    "GIT_AUTHOR_NAME": "test",
    "GIT_AUTHOR_EMAIL": "test@example.invalid",
    "GIT_COMMITTER_NAME": "test",
    "GIT_COMMITTER_EMAIL": "test@example.invalid",
}


def make_base_repo(tmp_path):
    env = dict(os.environ)
    env.update(GIT_ENV)
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, env=env)
    (tmp_path / "ci").mkdir()
    (tmp_path / "ci" / "existing.sh").write_text("#!/usr/bin/env bash\n")
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "existing.sh").write_text("#!/usr/bin/env bash\n")
    (tmp_path / ".gitlab-ci.yml").write_text("stages: []\n")
    (tmp_path / ".gitignore").write_text("*.log\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True, env=env)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-q", "-m", "base"], check=True, env=env)
    return tmp_path


def run_gate(repo_dir):
    return subprocess.run(
        ["bash", str(SCRIPT_PATH)],
        capture_output=True, text=True,
        env={"PATH": "/usr/bin:/bin", "REPO_DIR": str(repo_dir)},
    )


def test_clean_tree_passes(tmp_path):
    repo = make_base_repo(tmp_path)
    result = run_gate(repo)
    assert result.returncode == 0, result.stdout


def test_unstaged_edit_to_existing_ci_file_is_blocked(tmp_path):
    repo = make_base_repo(tmp_path)
    (repo / "ci" / "existing.sh").write_text("#!/usr/bin/env bash\necho changed\n")
    result = run_gate(repo)
    assert result.returncode == 1
    assert "ci/existing.sh" in result.stderr


def test_new_untracked_ci_file_is_blocked(tmp_path):
    repo = make_base_repo(tmp_path)
    (repo / "ci" / "new-gate.sh").write_text("#!/usr/bin/env bash\n")
    result = run_gate(repo)
    assert result.returncode == 1
    assert "ci/new-gate.sh" in result.stderr


def test_staged_edit_to_gitlab_ci_yml_is_blocked(tmp_path):
    repo = make_base_repo(tmp_path)
    (repo / ".gitlab-ci.yml").write_text("stages: [tests]\n")
    subprocess.run(["git", "-C", str(repo), "add", ".gitlab-ci.yml"], check=True)
    result = run_gate(repo)
    assert result.returncode == 1
    assert ".gitlab-ci.yml" in result.stderr


def test_gitignore_edit_is_blocked(tmp_path):
    repo = make_base_repo(tmp_path)
    (repo / ".gitignore").write_text("*.log\n*.tmp\n")
    result = run_gate(repo)
    assert result.returncode == 1
    assert ".gitignore" in result.stderr


def test_new_k8s_dir_is_blocked(tmp_path):
    repo = make_base_repo(tmp_path)
    (repo / "k8s").mkdir()
    (repo / "k8s" / "deploy.yaml").write_text("kind: Deployment\n")
    result = run_gate(repo)
    assert result.returncode == 1
    # git status --porcelain схлопывает новую untracked-директорию в "k8s/",
    # а не перечисляет файлы внутри — гейт видит ровно эту строку.
    assert "k8s/" in result.stderr


def test_edit_to_scripts_does_not_trip_the_gate(tmp_path):
    repo = make_base_repo(tmp_path)
    (repo / "scripts" / "existing.sh").write_text("#!/usr/bin/env bash\necho changed\n")
    (repo / "scripts" / "new-task.sh").write_text("#!/usr/bin/env bash\n")
    result = run_gate(repo)
    assert result.returncode == 0, result.stdout


def test_path_prefix_is_not_confused_with_ci_directory(tmp_path):
    """scripts/ci-helper.sh не должен матчиться как ci/ — регрессия на '^ci/' якорь."""
    repo = make_base_repo(tmp_path)
    (repo / "scripts" / "ci-helper.sh").write_text("#!/usr/bin/env bash\n")
    result = run_gate(repo)
    assert result.returncode == 0, result.stdout


def test_not_a_git_repo_errors_with_code_2(tmp_path):
    result = run_gate(tmp_path)
    assert result.returncode == 2
