"""Release-facing documentation and the Admin documentation allowlist agree."""
import asyncio
import importlib.util
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit

import pytest

from tests.test_readme_documentation import anchors

ROOT = Path(__file__).resolve().parents[1]
NEW_GUIDES = {
    "Calendly-calendar-tool.md",
    "In-Call-Summaries.md",
    "AGENTS.md",
    "ARI-Endpoint-Discovery.md",
    "Microsoft-teams-verification.md",
    "MIGRATION.md",
    "INTEGRATION_BACKUPS.md",
    "AGENTS_EXPERIENCE_ROLLOUT.md",
}


@pytest.fixture
def docs_api(monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "release_docs_api", ROOT / "admin_ui/backend/api/docs.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "get_docs_path", lambda: ROOT / "docs")
    return module


def test_documentation_catalog_is_unique_and_serves_release_guides(docs_api):
    files = [doc["file"] for cat in docs_api.DOC_CATEGORIES.values() for doc in cat["docs"]]
    assert len(files) == len(set(files))
    assert NEW_GUIDES.issubset(files)
    for relative in files:
        assert (ROOT / "docs" / relative).is_file(), relative
    for relative in NEW_GUIDES:
        result = asyncio.run(docs_api.get_doc_content(relative))
        assert result.content == (ROOT / "docs" / relative).read_text()
        assert result.title and result.category


def test_documentation_catalog_does_not_authorize_arbitrary_paths(docs_api):
    for path in ("../.env", "../../secrets/calendly.json", "/etc/passwd"):
        with pytest.raises(docs_api.HTTPException) as exc:
            asyncio.run(docs_api.get_doc_content(path))
        assert exc.value.status_code == 404


@pytest.mark.parametrize("relative", [
    "docs/INTEGRATION_BACKUPS.md",
    "docs/baselines/golden/v7.7.0-validation-matrix.md",
])
def test_new_release_guides_have_resolvable_local_links(relative):
    source = ROOT / relative
    for target in re.findall(r'\]\(([^\s)]+)', source.read_text()):
        parsed = urlsplit(target)
        if parsed.scheme or parsed.netloc:
            continue
        path = (source.parent / unquote(parsed.path)).resolve() if parsed.path else source
        assert path.is_file(), target
        if parsed.fragment:
            assert unquote(parsed.fragment) in anchors(path.read_text()), target


def test_new_contributors_have_matching_profile_badges_and_credit():
    readme = (ROOT / "README.md").read_text()
    credits = (ROOT / "CONTRIBUTORS.md").read_text()
    for login in ("pcbeingused333", "lucamancusodev"):
        assert f'href="https://github.com/{login}"' in readme
        assert f'src="https://github.com/{login}.png"' in readme
        assert f"(https://github.com/{login})" in credits
    rows = re.findall(r'<td\b', readme)
    assert len(rows) == 25
    contributor_table = credits.split("| Avatar |", 1)[1].split("<!-- markdownlint-restore -->", 1)[0]
    assert "\n\n|" not in contributor_table
    assert "Reports and Requirements" in credits


def test_upgrade_and_migration_keep_legacy_links_and_explicit_waiver():
    installation = (ROOT / "docs/INSTALLATION.md").read_text()
    for version in ("762", "763", "764", "770"):
        assert f"upgrade-to-v{version}-existing-checkout" in anchors(installation)
    migration = (ROOT / "docs/MIGRATION.md").read_text()
    assert "v764-to-v770" in anchors(migration)
    matrix = (ROOT / "docs/baselines/golden/v7.7.0-validation-matrix.md").read_text()
    assert "WAIVED explicitly for v7.7.0" in matrix
    assert "Merge approval | HOLD" in matrix
    assert "Tag/date | HOLD" in matrix
