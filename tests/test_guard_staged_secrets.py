"""Тесты pre-commit-гейта секретов (ci/guard-staged-secrets.sh).

Единственное, что стоит между автономным коммитом и утёкшим секретом —
регэксп по ИМЕНИ файла в индексе (git diff --cached). До 2026-08-06 паттерн
`(^|/)\\.env` пропускал файлы вроде `database.env` (точка не сразу после `/`
или начала строки) — тест test_env_suffixed_filename_is_blocked фиксирует
именно этот регресс.
"""

import os
import pathlib
import subprocess

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "ci" / "guard-staged-secrets.sh"

GIT_ENV = {
    "GIT_AUTHOR_NAME": "test",
    "GIT_AUTHOR_EMAIL": "test@example.invalid",
    "GIT_COMMITTER_NAME": "test",
    "GIT_COMMITTER_EMAIL": "test@example.invalid",
}


def make_repo_with_staged_file(tmp_path, rel_path, content="x"):
    """git init + один закоммиченный файл-якорь, затем rel_path staged (не закоммичен)."""
    env = dict(os.environ)
    env.update(GIT_ENV)
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, env=env)
    (tmp_path / "README.md").write_text("anchor\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "README.md"], check=True, env=env)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-q", "-m", "anchor"], check=True, env=env)

    target = tmp_path / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    subprocess.run(["git", "-C", str(tmp_path), "add", rel_path], check=True, env=env)
    return tmp_path


def run_guard(repo_dir):
    return subprocess.run(
        ["bash", str(SCRIPT_PATH)],
        capture_output=True, text=True,
        env={"PATH": "/usr/bin:/bin", "REPO_DIR": str(repo_dir)},
    )


@pytest.mark.parametrize("rel_path", [".env", ".env.local", ".env.production", "config/.env"])
def test_dotenv_variants_are_blocked(tmp_path, rel_path):
    repo = make_repo_with_staged_file(tmp_path, rel_path)
    result = run_guard(repo)
    assert result.returncode == 1, result.stdout


@pytest.mark.parametrize("rel_path", ["database.env", "myapp.env", "backend/secrets.env"])
def test_env_suffixed_filename_is_blocked(tmp_path, rel_path):
    """Регресс-тест: старый паттерн `(^|/)\\.env` этого не ловил."""
    repo = make_repo_with_staged_file(tmp_path, rel_path)
    result = run_guard(repo)
    assert result.returncode == 1, result.stdout


@pytest.mark.parametrize("rel_path", [".env.example", ".env.template"])
def test_dotenv_template_variants_are_allowed(tmp_path, rel_path):
    repo = make_repo_with_staged_file(tmp_path, rel_path)
    result = run_guard(repo)
    assert result.returncode == 0, result.stdout


@pytest.mark.parametrize("rel_path", ["server.pem", "client.key", "ssh/id_rsa"])
def test_key_material_is_blocked(tmp_path, rel_path):
    repo = make_repo_with_staged_file(tmp_path, rel_path)
    result = run_guard(repo)
    assert result.returncode == 1, result.stdout


def test_ordinary_file_passes(tmp_path):
    repo = make_repo_with_staged_file(tmp_path, "scripts/example.sh")
    result = run_guard(repo)
    assert result.returncode == 0, result.stdout


def test_not_a_git_repo_errors_with_code_2(tmp_path):
    result = run_guard(tmp_path)
    assert result.returncode == 2
