"""Static checks of the GitHub Actions workflows (CI on develop, release build, Pages deploy).

The workflows run only on GitHub, so their structure is checked here: one wheel and one site per
CI run shared by every test group, offline Pyodide tests, headless browsers only, a release job
that creates drafts and never publishes, and a Pages job whose artifact name depends on the run
attempt so that "Re-run all jobs" can repair a deployment.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
ATTEMPT_ARTIFACT = "github-pages-${{ github.run_attempt }}"
TEST_GROUPS = ("pyodide", "web", "e2e", "cross-browser")


def load(name: str) -> dict:
    path = WORKFLOWS / name
    if not path.is_file():
        pytest.skip(f"{name} does not exist")
    data: dict = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["on"] = data.pop(True) if True in data else data.get("on")
    return data


def steps(job: dict) -> list[dict]:
    found: list[dict] = job.get("steps", [])
    return found


def action(step: dict) -> str:
    return str(step.get("uses", "")).split("@")[0]


def runs(job: dict) -> str:
    return "\n".join(step.get("run", "") for step in steps(job))


def uses(job: dict, name: str) -> list[dict]:
    return [step for step in steps(job) if action(step) == name]


def all_workflow_text() -> str:
    return "\n".join(path.read_text(encoding="utf-8") for path in sorted(WORKFLOWS.glob("*.yml")))


def test_every_action_is_pinned_to_a_major_release_tag():
    for path in sorted(WORKFLOWS.glob("*.yml")):
        data = load(path.name)
        for job_name, job in data["jobs"].items():
            if "uses" in job:
                assert job["uses"].startswith("./.github/workflows/"), (path.name, job_name)
                continue
            for step in steps(job):
                if "uses" in step:
                    assert re.fullmatch(r"actions/[a-z-]+@v\d+", step["uses"]), (
                        path.name,
                        job_name,
                        step["uses"],
                    )


def test_no_headed_browser_and_no_private_tool_in_any_workflow():
    text = all_workflow_text()
    for banned in ("--headed", "--ui", "--debug", "PWDEBUG", "show-report", "codegen", "xdg-open"):
        assert banned not in text, banned
    assert "check_repo_hygiene" not in text


def test_every_checkout_is_this_repository():
    paths = sorted(WORKFLOWS.glob("*.yml"))
    assert paths
    for path in paths:
        for job_name, job in load(path.name)["jobs"].items():
            for step in uses(job, "actions/checkout"):
                assert "repository" not in (step.get("with") or {}), (path.name, job_name)


class TestCI:
    def test_triggers_include_develop_and_the_release_build(self):
        data = load("ci.yml")
        assert set(data["on"]["push"]["branches"]) == {"main", "develop"}
        assert data["on"]["pull_request"]["branches"] == ["main"]
        assert "workflow_call" in data["on"]
        assert data["permissions"] == {"contents": "read"}

    def test_one_build_job_makes_the_wheel_and_the_site_once(self):
        jobs = load("ci.yml")["jobs"]
        build = jobs["build"]
        assert "strategy" not in build
        assert build["runs-on"] == "ubuntu-22.04"
        python = uses(build, "actions/setup-python")
        assert [step["with"]["python-version"] for step in python] == ["3.10.12"]
        script = runs(build)
        assert (
            script.index("chmod -R go-w .")
            < script.index("umask 022")
            < script.index("python -m build")
        )
        assert 'SOURCE_DATE_EPOCH="$(git log -1 --format=%ct)" python -m build' in script
        assert '"build==1.5.0"' in script
        assert "node web/scripts/build_site.mjs --out web/dist/ci/site" in script
        assert '--commit "$(git rev-parse HEAD)"' in script
        assert "node web/scripts/check_site.mjs" in script
        assert "node web/scripts/assemble_pages.mjs zip" in script
        uploaded = {step["with"]["name"] for step in uses(build, "actions/upload-artifact")}
        assert uploaded == {"dist", "site"}
        assert build["outputs"]["version"] == "${{ steps.dist.outputs.version }}"
        for name, job in jobs.items():
            if name != "build":
                assert "python -m build" not in runs(job), name
                assert "build_site.mjs" not in runs(job), name

    def test_every_test_group_uses_the_build_jobs_wheel_and_site(self):
        jobs = load("ci.yml")["jobs"]
        for name in ("pyodide", "e2e", "cross-browser"):
            job = jobs[name]
            assert job["needs"] == "build", name
            downloaded = {step["with"]["name"] for step in uses(job, "actions/download-artifact")}
            assert "site" in downloaded, name
            assert 'unzip -q "site/sarcostat-web-$VERSION.zip" -d web/dist/ci/site' in runs(job), (
                name
            )
            assert 'cmp site/BUILD.json "web/dist/ci/site/v$VERSION/BUILD.json"' in runs(job), name
            assert job["env"]["VERSION"] == "${{ needs.build.outputs.version }}", name
        assert {
            step["with"]["name"] for step in uses(jobs["pyodide"], "actions/download-artifact")
        } == {"dist", "site"}
        assert jobs["install"]["needs"] == "build"

    def test_node_comes_from_web_nvmrc_everywhere(self):
        for name in ("ci.yml", "release-build.yml", "pages.yml"):
            for job_name, job in load(name)["jobs"].items():
                for step in uses(job, "actions/setup-node"):
                    assert step["with"]["node-version-file"] == "web/.nvmrc", (name, job_name)
                    assert "node-version" not in step["with"], (name, job_name)

    def test_pyodide_group_is_offline_on_a_cached_and_verified_test_subset(self):
        job = load("ci.yml")["jobs"]["pyodide"]
        cache = uses(job, "actions/cache")
        assert [step["with"] for step in cache] == [
            {
                "path": "web/.cache/pyodide-test",
                "key": "pyodide-test-${{ hashFiles('web/pyodide-test-files.sha256') }}",
            }
        ]
        names = [step.get("name", step.get("uses", "")) for step in steps(job)]
        verify = next(
            i
            for i, step in enumerate(steps(job))
            if step.get("run") == "npm run pyodide:test-subset"
        )
        assert verify > names.index("Restore the Pyodide test subset")
        script = runs(job)
        assert "node tests/pyodide/run_pytest.mjs" in script
        assert "--tests core,io,engine --offline" in script
        assert script.count("node tests/pyodide/parity.mjs") == 2
        assert "--via analysis" in script and "--via bridge" in script
        assert script.count("--rule ../tests/golden/web/tolerance.json --offline") == 2
        assert "node tests/pyodide/site_boot.mjs" in script and "--offline" in script
        upload = uses(job, "actions/upload-artifact")
        assert upload[0]["if"] == "${{ !cancelled() }}"

    def test_build_job_caches_the_site_subset_by_its_hash_file(self):
        build = load("ci.yml")["jobs"]["build"]
        assert [step["with"] for step in uses(build, "actions/cache")] == [
            {
                "path": "web/.cache/pyodide-site",
                "key": "pyodide-site-${{ hashFiles('web/pyodide-files.sha256') }}",
            }
        ]

    def test_web_group_typechecks_and_runs_unit_tests(self):
        job = load("ci.yml")["jobs"]["web"]
        script = runs(job)
        assert (
            "npm ci" in script and "npm run typecheck" in script and "npm run test:unit" in script
        )

    def test_e2e_runs_headless_chromium_and_keeps_screenshots_without_comparing(self):
        job = load("ci.yml")["jobs"]["e2e"]
        assert job["env"]["CI"] == "true"
        assert job["env"]["PLAYWRIGHT_HTML_OPEN"] == "never"
        assert job["env"]["PORT"] == "0"
        script = runs(job)
        assert "npx playwright install --with-deps chromium" in script
        assert "firefox" not in script and "webkit" not in script
        assert "npm run test:e2e" in script and "npm run test:shots" in script
        artefacts = {step["with"]["name"]: step for step in uses(job, "actions/upload-artifact")}
        assert set(artefacts) == {"e2e-logs", "screenshots", "e2e-report"}
        assert artefacts["e2e-report"]["if"] == "${{ failure() }}"
        assert "**/*.json" in artefacts["e2e-logs"]["with"]["path"]

    def test_cross_browser_job_runs_firefox_and_webkit_in_ci_only(self):
        jobs = load("ci.yml")["jobs"]
        job = jobs["cross-browser"]
        assert job["env"]["CI"] == "true"
        script = runs(job)
        assert "npx playwright install --with-deps firefox webkit" in script
        assert "--project=firefox --project=webkit --pass-with-no-tests" in script
        assert "env -u DISPLAY -u WAYLAND_DISPLAY" in script
        names = {step["with"]["name"] for step in uses(job, "actions/upload-artifact")}
        assert names == {"cross-browser-logs", "cross-browser-report"}
        for name, other in jobs.items():
            if name != "cross-browser":
                assert "firefox" not in runs(other), name

    def test_python_group_is_kept(self):
        jobs = load("ci.yml")["jobs"]
        assert jobs["test"]["needs"] == "resolve"
        assert jobs["test"]["strategy"]["matrix"] == "${{ fromJSON(needs.resolve.outputs.matrix) }}"
        assert "python -m mypy src/sarcostat" in runs(jobs["test"])
        assert "python -m pytest tests/" in runs(jobs["test"])
        assert "ruff check ." in runs(jobs["lint"]) and "ruff format --check ." in runs(
            jobs["lint"]
        )
        assert "bash tools/check_install.sh dist/*.whl" in runs(jobs["install"])
        assert set(jobs) == {"resolve", "test", "lint", "build", "install", *TEST_GROUPS}

    def test_new_jobs_have_time_limits_within_the_ci_budget(self):
        jobs = load("ci.yml")["jobs"]
        for name in ("build", *TEST_GROUPS):
            assert 0 < jobs[name]["timeout-minutes"] <= 20, name


class TestReleaseBuild:
    def test_runs_on_v_tags_only(self):
        data = load("release-build.yml")
        assert data["on"] == {"push": {"tags": ["v*"]}}
        assert data["permissions"] == {"contents": "read"}
        assert data["concurrency"]["cancel-in-progress"] is False

    def test_refuses_a_tag_that_differs_from_v_plus_version(self):
        jobs = load("release-build.yml")["jobs"]
        script = runs(jobs["tag"])
        assert (
            'assemble_pages.mjs release-info --tag "$GITHUB_REF_NAME" --init src/sarcostat/__init__.py'
            in script
        )
        assert jobs["tag"]["outputs"]["prerelease"] == "${{ steps.info.outputs.prerelease }}"

    def test_reuses_ci_for_the_build_and_the_three_test_groups(self):
        jobs = load("release-build.yml")["jobs"]
        assert jobs["ci"]["uses"] == "./.github/workflows/ci.yml"
        assert jobs["ci"]["needs"] == "tag"
        assert jobs["ci"]["permissions"] == {"contents": "read"}
        assert set(jobs["release"]["needs"]) == {"tag", "ci"}

    def test_creates_a_draft_with_the_four_assets_and_prerelease_from_the_version(self):
        job = load("release-build.yml")["jobs"]["release"]
        script = runs(job)
        assert 'gh release create "$GITHUB_REF_NAME" "${flags[@]}"' in script
        assert "flags=(--draft --verify-tag" in script
        assert 'if [ "$PRERELEASE" = "true" ]; then' in script and "flags+=(--prerelease)" in script
        assert job["env"]["PRERELEASE"] == "${{ needs.tag.outputs.prerelease }}"
        for asset in (
            '"dist/sarcostat-$VERSION-py3-none-any.whl"',
            '"dist/sarcostat-$VERSION.tar.gz"',
            '"site/sarcostat-web-$VERSION.zip"',
            "site/BUILD.json",
        ):
            assert asset in script.split("gh release create", 1)[1], asset
        assert "assemble_pages.mjs verify-zip" in script

    def test_contains_no_publish_step(self):
        text = (WORKFLOWS / "release-build.yml").read_text(encoding="utf-8")
        for banned in (
            "--draft=false",
            "draft=false",
            '"draft": false',
            "draft: false",
            "gh release edit",
            "PATCH",
            "--method",
            "gh release upload",
        ):
            assert banned not in text, banned

    def test_contents_write_is_granted_to_the_release_job_only(self):
        jobs = load("release-build.yml")["jobs"]
        writers = [
            name
            for name, job in jobs.items()
            if job.get("permissions", {}).get("contents") == "write"
        ]
        assert writers == ["release"]
        assert jobs["release"]["permissions"] == {"contents": "write"}


class TestPages:
    def test_triggers_and_concurrency(self):
        data = load("pages.yml")
        assert data["on"] == {"release": {"types": ["published"]}, "workflow_dispatch": None}
        assert data["concurrency"] == {"group": "pages", "cancel-in-progress": False}
        assert data["permissions"] == {"contents": "read"}

    def test_workflow_dispatch_on_a_non_tag_ref_is_refused_early(self):
        jobs = load("pages.yml")["jobs"]
        check = runs(jobs["ref"])
        assert 'case "$GITHUB_REF" in' in check and "refs/tags/v*)" in check and "exit 1" in check
        assert jobs["deploy"]["needs"] == "ref"
        assert jobs["deploy"]["if"] == "startsWith(github.ref, 'refs/tags/v')"

    def test_artifact_name_depends_on_the_run_attempt_in_upload_and_deploy(self):
        job = load("pages.yml")["jobs"]["deploy"]
        upload = uses(job, "actions/upload-pages-artifact")
        deploy = uses(job, "actions/deploy-pages")
        assert len(upload) == 1 and len(deploy) == 1
        assert upload[0]["with"]["name"] == ATTEMPT_ARTIFACT
        assert deploy[0]["with"]["artifact_name"] == ATTEMPT_ARTIFACT
        assert int(deploy[0]["with"]["timeout"]) <= 600000
        assert steps(job).index(upload[0]) < steps(job).index(deploy[0])

    def test_deploy_job_environment_and_permissions(self):
        jobs = load("pages.yml")["jobs"]
        job = jobs["deploy"]
        assert job["environment"]["name"] == "github-pages"
        assert job["permissions"] == {"contents": "read", "pages": "write", "id-token": "write"}
        assert "permissions" not in jobs["ref"]

    def test_assembler_gets_the_event_the_attempt_the_ref_and_web_releases_json(self):
        script = runs(load("pages.yml")["jobs"]["deploy"])
        for part in (
            '--event "$GITHUB_EVENT_NAME"',
            '--run-attempt "$GITHUB_RUN_ATTEMPT"',
            '--ref "$GITHUB_REF"',
            "--known web/releases.json",
            "--out _site",
        ):
            assert part in script, part
        assert "assemble_pages.mjs plan" in script and "assemble_pages.mjs live-list" in script
        assert "versions.json" in script and "-w '%{http_code}'" in script

    def test_hashes_are_never_fetched_with_a_bare_accept_encoding_header(self):
        for path in (
            WORKFLOWS / "pages.yml",
            ROOT / "web" / "scripts" / "verify_deploy.sh",
            ROOT / "web" / "scripts" / "fetch_deployed.sh",
        ):
            if path.is_file():
                assert "Accept-Encoding" not in path.read_text(encoding="utf-8").replace(
                    "Accept-Encoding header", ""
                ), path.name
