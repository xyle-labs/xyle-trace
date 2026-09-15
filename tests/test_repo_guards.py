"""Exercise the repository path policy against a real Git index and history."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

CHECK = Path(__file__).resolve().parents[1] / "scripts" / "check_repo.py"


@pytest.fixture
def repo(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "config", "user.name", "Xyle Labs"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "jesse@xyle.de"], cwd=tmp_path, check=True)
    return tmp_path


def check(repo, *args):
    return subprocess.run([sys.executable, str(CHECK), *args], cwd=repo, capture_output=True, check=False)


@pytest.mark.parametrize("name", [".env", "nested/.env.production", ".lineage/snapshots/blob",
                                 "projects/private.py", ".superpowers/session.md",
                                 "service.key", "graph.db-wal", ".aws/credentials",
                                 ".codex/sessions/run.jsonl", ".claude/history.jsonl",
                                 "nested/transcripts/chat.md", "chat-session.txt",
                                 "dump.jsonl", "backup.zip", "report.pdf",
                                 ".config/gcloud/application_default_credentials.json",
                                 ".git-credentials", "debug.log", ".aider.chat.history.md",
                                 "nested/misc/private.txt"])
def test_forced_sensitive_paths_fail_even_without_secret_content(repo, name):
    path = repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("synthetic fixture")
    subprocess.run(["git", "add", "--", name], cwd=repo, check=True)
    assert check(repo, "--staged").returncode == 1


def test_source_and_placeholder_env_are_allowed(repo):
    for name in (".env.example", "example.py"):
        (repo / name).write_text("placeholder")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    assert check(repo, "--staged").returncode == 0


def test_approved_contact_is_allowed_in_content_messages_and_history(repo):
    subprocess.run(["git", "config", "user.email", "jesse@xyle.de"], cwd=repo, check=True)
    (repo / "contact.md").write_text("Contact: [jesse@xyle.de](mailto:jesse@xyle.de)")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    assert check(repo, "--staged").returncode == 0
    assert check(repo, "--commit-msg", str(repo / "contact.md")).returncode == 0
    subprocess.run(["git", "-c", "commit.gpgsign=false", "commit", "-qm",
                    "Contact: jesse@xyle.de"], cwd=repo, check=True)
    assert check(repo, "--history").returncode == 0


def test_former_project_email_is_rejected(repo):
    subprocess.run(["git", "config", "user.email", "noreply@" + "xyle.de"],
                   cwd=repo, check=True)
    assert check(repo, "--staged").returncode == 1


def test_deleted_private_file_is_still_rejected_in_history(repo):
    (repo / ".env").write_text("synthetic fixture")
    env = {**os.environ, "GIT_AUTHOR_NAME": "Xyle Labs", "GIT_AUTHOR_EMAIL": "jesse@xyle.de",
           "GIT_COMMITTER_NAME": "Xyle Labs", "GIT_COMMITTER_EMAIL": "jesse@xyle.de"}
    for command in (["add", "."], ["-c", "commit.gpgsign=false", "commit", "-qm", "fixture"],
                    ["rm", ".env"], ["-c", "commit.gpgsign=false", "commit", "-qm", "remove"]):
        subprocess.run(["git", *command], cwd=repo, env=env, check=True, capture_output=True)
    assert check(repo).returncode == 0
    assert check(repo, "--history").returncode == 1


@pytest.mark.parametrize("author,expected", [("Claude <noreply@" + "anthropic.com>", 1),
                                            ("Human <human@example.com>", 0)])
def test_commit_attribution_preserves_human_coauthors(repo, author, expected):
    message = repo / "message"
    message.write_text(f"Change\n\nCo-Authored-By: {author}\n")
    assert check(repo, "--commit-msg", str(message)).returncode == expected


@pytest.mark.parametrize("content", [
    "contact: " + "private.person@" + "mail.invalid",
    "contact: " + "jesse@xyle.de" + ".invalid",
    "/" + "Users/fixture-person/private/file",
    "C:" + "\\Users\\fixture-person\\file",
    "https://" + "claude.ai/share/fixture-session",
    "https://" + "chatgpt.com/c/fixture-session",
    '{"role": ' + '"user", "content": "fixture"}',
])
def test_staged_content_is_scanned_and_findings_are_redacted(repo, content):
    path = repo / "notes.md"
    path.write_text(content)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    path.write_text("safe working copy conceals unsafe index")
    result = check(repo, "--staged")
    assert result.returncode == 1
    assert content.encode() not in result.stdout + result.stderr


def test_deleted_personal_content_is_rejected_in_history(repo):
    path = repo / "notes.md"
    path.write_text("person@" + "mail.invalid")
    for command in (["add", "."], ["-c", "commit.gpgsign=false", "commit", "-qm", "fixture"],
                    ["rm", "notes.md"], ["-c", "commit.gpgsign=false", "commit", "-qm", "remove"]):
        subprocess.run(["git", *command], cwd=repo, check=True, capture_output=True)
    assert check(repo, "--history").returncode == 1


def test_commit_message_is_scanned_for_session_links(repo):
    message = repo / "message"
    message.write_text("See https://" + "chatgpt.com/share/fixture-session")
    assert check(repo, "--commit-msg", str(message)).returncode == 1


def test_personal_commit_identity_is_rejected(repo):
    subprocess.run(["git", "config", "user.email", "person@" + "mail.invalid"], cwd=repo, check=True)
    assert check(repo, "--staged").returncode == 1


def test_binary_disguised_as_source_is_rejected(repo):
    (repo / "fixture.py").write_bytes(b"PK\x00\x03private archive")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    assert check(repo, "--staged").returncode == 1


def test_symlinks_are_rejected_without_reading_target(repo):
    (repo / "outside").write_text("safe fixture")
    (repo / "link.py").symlink_to("outside")
    subprocess.run(["git", "add", "link.py"], cwd=repo, check=True)
    assert check(repo, "--staged").returncode == 1


def test_untracked_working_content_is_scanned(repo):
    (repo / "notes.md").write_text("person@" + "mail.invalid")
    assert check(repo).returncode == 1


def test_annotated_tag_metadata_is_scanned(repo):
    subprocess.run(["git", "-c", "commit.gpgsign=false", "commit", "--allow-empty", "-qm",
                    "fixture"], cwd=repo, check=True)
    subprocess.run(["git", "-c", "tag.gpgsign=false", "tag", "-a", "v0", "-m",
                    "contact person@" + "mail.invalid"], cwd=repo, check=True)
    assert check(repo, "--history").returncode == 1


def test_shallow_history_is_rejected(repo, tmp_path_factory):
    subprocess.run(["git", "-c", "commit.gpgsign=false", "commit", "--allow-empty", "-qm",
                    "fixture"], cwd=repo, check=True)
    clone = tmp_path_factory.mktemp("clone") / "shallow"
    subprocess.run(["git", "clone", "-q", "--depth=1", repo.as_uri(), str(clone)], check=True)
    assert check(clone, "--history").returncode == 1


def test_tree_snapshot_refs_are_scanned(repo):
    (repo / "notes.md").write_text("person@" + "mail.invalid")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    tree = subprocess.check_output(["git", "write-tree"], cwd=repo, text=True).strip()
    subprocess.run(["git", "update-ref", "refs/checkpoints/fixture", tree], cwd=repo, check=True)
    subprocess.run(["git", "rm", "-f", "notes.md"], cwd=repo, check=True)
    assert check(repo, "--history").returncode == 1
