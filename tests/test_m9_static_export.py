"""The static export (M9): the API's recorded answers for the static, read-only dashboard.

The committed export (``web/static-export``) must be exactly what a new export of the
committed research outputs produces, each file the API's own answer to its request (asked
here through the test client, independently of the exporter's in-process driver), with
every integrity label and limitation kept, nothing operational, local or secret in it, and
nothing written when anything is wrong.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from atlas.api import static_export
from atlas.api.static_export import (
    DEFAULT_OUTPUT,
    EXPORT_FORMAT,
    HISTORY_WINDOWS,
    MANIFEST_NAME,
    Answer,
    Recorder,
    StaticExportError,
    check_answer,
    export_settings,
    unsafe_text,
    verify_static_export,
    write_static_export,
)
from atlas.cli import main
from atlas.dashboard import DASHBOARD_CSP
from tests.conftest import REPO_ROOT
from tests.m5_helpers import add_api_inputs, client

if TYPE_CHECKING:
    from atlas.api.reference import ReferenceStore

COMMITTED = REPO_ROOT / DEFAULT_OUTPUT
FIXTURES = REPO_ROOT / "web" / "src" / "test" / "fixtures"
SNAPSHOT = "legacy-v0"


def _manifest(directory: Path = COMMITTED) -> dict[str, Any]:
    document = json.loads((directory / MANIFEST_NAME).read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    return document


def _answer(file: str) -> Answer:
    request = _manifest()["files"][file]["request"]
    return Answer(file=file, request=request, body=(COMMITTED / file).read_bytes())


def _with(answer: Answer, change: Any) -> Answer:
    document = answer.document()
    change(document)
    return Answer(answer.file, answer.request, json.dumps(document).encode("utf-8"))


@pytest.fixture(scope="module")
def fresh(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A new export of the committed outputs, made once for the module."""
    output = tmp_path_factory.mktemp("static") / "export"
    report = write_static_export(REPO_ROOT, SNAPSHOT, output)
    assert report.ok
    return output


# --- The committed export --------------------------------------------------------------


def test_the_committed_export_verifies() -> None:
    reports = verify_static_export(REPO_ROOT, SNAPSHOT, COMMITTED)
    assert [r.name for r in reports] == ["static export files", "static export reproduction"]
    assert all(r.ok for r in reports), [i.message for r in reports for i in r.issues]
    assert reports[1].facts["identical"] is True


def test_a_new_export_is_byte_identical(fresh: Path) -> None:
    names = sorted(p.name for p in fresh.iterdir())
    assert names == sorted(p.name for p in COMMITTED.iterdir())
    for name in names:
        assert (fresh / name).read_bytes() == (COMMITTED / name).read_bytes(), name


def test_each_file_is_the_api_answer_to_its_request(api_store: ReferenceStore) -> None:
    files = _manifest()["files"]
    with client(api_store) as test_client:
        for name, entry in files.items():
            answer = test_client.get(entry["request"])
            assert answer.status_code == 200, entry["request"]
            assert answer.content == (COMMITTED / name).read_bytes(), name
            body = (COMMITTED / name).read_bytes()
            assert hashlib.sha256(body).hexdigest() == entry["sha256"]
            assert len(body) == entry["bytes"]
            integrity = json.loads(body)["integrity"]
            assert entry["data_status"] == integrity["data_status"]
            assert entry["record_type"] == integrity["record_type"]


def test_it_shares_the_dashboard_fixtures_bytes() -> None:
    shared = sorted({p.name for p in FIXTURES.glob("*.json")} & set(_manifest()["files"]))
    assert len(shared) >= 10
    for name in shared:
        assert (FIXTURES / name).read_bytes() == (COMMITTED / name).read_bytes(), name


def test_the_export_covers_the_static_pages_and_nothing_operational() -> None:
    manifest = _manifest()
    assert manifest["format"] == EXPORT_FORMAT
    requests = {entry["request"] for entry in manifest["files"].values()}
    assert len(requests) == len(manifest["files"]) == 24
    assert not any(r.startswith(("/api/v1/system", "/health", "/openapi")) for r in requests)
    assert not any(r == "/api/v1/simulations" for r in requests)
    left_out = " ".join(item["request"] for item in manifest["not_exported"])
    for request in ("GET /api/v1/system", "POST /api/v1/simulations", "/health"):
        assert request in left_out
    scenarios = json.loads((COMMITTED / "scenarios.json").read_bytes())["data"]["scenarios"]
    for scenario in scenarios:
        assert f"/api/v1/scenarios/{scenario['scenario_id']}" in requests
    periods = json.loads((COMMITTED / "metrics-bucket.json").read_bytes())["data"]["periods"]
    for period in periods:
        assert f"/api/v1/forecasts/metrics?scope=horizon&period={period['name']}" in requests


def test_the_history_windows_are_the_forecasting_pages() -> None:
    """The page asks for the default (180 days) and these starts: the export must hold them."""
    from atlas.api.views import DEFAULT_HISTORY_DAYS

    page = (REPO_ROOT / "web" / "src" / "views" / "Forecasting.tsx").read_text(encoding="utf-8")
    windows = re.search(r"const WINDOWS = \[(.*?)\] as const;", page, re.DOTALL)
    assert windows is not None
    values = re.findall(r"value: '([a-z0-9]+)'", windows.group(1))
    assert values == ["90", "180", "365", "all"]
    assert DEFAULT_HISTORY_DAYS == 180
    assert HISTORY_WINDOWS == (90, 365, None)
    requests = {e["request"] for e in _manifest()["files"].values()}
    assert {
        "/api/v1/forecasts/history",
        "/api/v1/forecasts/history?start=2026-04-02",
        "/api/v1/forecasts/history?start=2025-07-01",
        "/api/v1/forecasts/history?start=2023-01-01",
    } <= requests
    days = {
        name: len(json.loads((COMMITTED / name).read_bytes())["data"]["points"])
        for name in ("history.json", "history-90d.json", "history-365d.json", "history-all.json")
    }
    assert days == {
        "history.json": 180,
        "history-90d.json": 90,
        "history-365d.json": 365,
        "history-all.json": 1277,
    }


# --- Integrity and disclosures ---------------------------------------------------------


def test_the_scientific_status_and_every_label_are_kept() -> None:
    manifest = _manifest()
    assert manifest["scientific_status"] == {
        "point_forecast_validated": False,
        "validated_recommendation": False,
        "uncertainty_interval_status": "failed_calibration",
        "uncertainty_decision_grade": False,
        "m2_recommended_model": "atlas_linear_regression",
        "m2_h1_significant_after_holm": False,
        "m2_h1_holm_adjusted_p_value": pytest.approx(0.0535166597203122),
        "m2_selection_evidence_notice": True,
        "projection_record_type": "future_projection",
        "projection_origin": "2026-06-30",
        "projection_first_target": "2026-07-01",
        "projection_last_target": "2026-07-30",
        "projection_archived_notice": True,
        "m4_parameter_provenance": "HYPOTHETICAL",
        "m4_scenarios": 9,
        "m4_scenarios_meeting_every_constraint": 3,
    }
    for name, entry in manifest["files"].items():
        integrity = json.loads((COMMITTED / name).read_bytes())["integrity"]
        assert integrity["validated_recommendation"] is False, name
        assert integrity["point_forecast_validated"] is False, name
        assert integrity["uncertainty_interval_status"] == "failed_calibration", name
        assert integrity["uncertainty_decision_grade"] is False, name
        assert integrity["notices"], name
        if entry["request"].startswith(("/api/v1/scenarios", "/api/v1/simulations")):
            assert integrity["parameter_provenance"] == "HYPOTHETICAL", name
    models = json.loads((COMMITTED / "models.json").read_bytes())
    # The M8 erratum travels with the selection it qualifies.
    assert any(n.startswith("M2 SELECTION EVIDENCE") for n in models["integrity"]["notices"])
    for name in ("forecast.json", "intervals.json"):
        notices = json.loads((COMMITTED / name).read_bytes())["integrity"]["notices"]
        assert any(n.startswith("ARCHIVED PROJECTION") for n in notices), name


def test_every_source_is_repository_relative_and_matches_the_committed_file() -> None:
    sources = _manifest()["sources"]
    assert {s["kind"] for s in sources} >= {
        "legacy_snapshot",
        "m2_backtest_run",
        "m3_calibration_status",
        "m4_reference_run",
    }
    for source in sources:
        path = Path(source["path"])
        assert not path.is_absolute()
        assert ".." not in path.parts
        for name, sha256 in source["files"].items():
            committed = (REPO_ROOT / path / name).read_bytes()
            assert hashlib.sha256(committed).hexdigest() == sha256, (path, name)


def _keys(value: object) -> set[str]:
    if isinstance(value, dict):
        return {str(k) for k in value} | {k for item in value.values() for k in _keys(item)}
    if isinstance(value, list):
        return {k for item in value for k in _keys(item)}
    return set()


def test_nothing_operational_local_or_secret_is_published() -> None:
    operational = {
        "simulation_workers",
        "max_body_bytes",
        "max_concurrent_simulations",
        "simulation_requires_api_key",
        "endpoints",
        "solver",
        "components",
    }
    for path in sorted(COMMITTED.iterdir()):
        document = json.loads(path.read_bytes())
        assert unsafe_text(document, REPO_ROOT) == [], path.name
        if path.name != MANIFEST_NAME:
            assert not (_keys(document) & operational), path.name


@pytest.mark.parametrize(
    ("text", "finding"),
    [
        ("written to /home/alice/atlas", "an absolute local path"),
        ("see /tmp/x", "an absolute local path"),
        (r"C:\Users\alice", "a Windows path"),
        ("file:///etc/passwd", "a file URL"),
        ("http://localhost:8000/api", "a local host name"),
        ("atlas.internal", "a local host name"),
        ("10.0.0.7", "an IP address"),
        ("[::1]:8000", "an IP address"),
        ("owner@example.org", "an e-mail address"),
        ("-----BEGIN RSA PRIVATE KEY-----", "a private key"),
        ("X-API-Key: abc", "a credential word"),
        ("the password is", "a credential word"),
        ("Authorization: Bearer x", "a credential word"),
        ("access token", "a credential word"),
        ("/var/lib/atlas", "an absolute local path"),
        ("/__w/demand-forecasting-model", "an absolute local path"),
        ("/usr/local/share", "an absolute local path"),
        ("C:/Users/alice", "a Windows path"),
        (r"\\fileserver\share", "a network share"),
        ("https://alice:secret@example.org/", "a URL with credentials"),
        ("build-07.corp", "an internal host name"),
        ("2001:0db8:85a3:0000:0000:8a2e:0370:7334", "an IPv6 address"),
        ("ghp_abcdefghijklmnopqrstuvwxyz0123456789", "a token"),
        ("AKIAIOSFODNN7EXAMPLE", "a token"),
        ("xoxb-1234567890-abcdefghij", "a token"),
        ("eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.sig", "a token"),
        # Second review round.
        ("2001:db8::abcd", "an IPv6 address"),
        ("fe80::1ff:fe23:4567:890a", "an IPv6 address"),
        ("2001:db8:85a3::8a2e:370:7334", "an IPv6 address"),
        ("D:/projects/atlas", "a Windows path"),
        ("redis://:password@cache.example.org:6379", "a URL with credentials"),
    ],
)
def test_unsafe_text_is_found(text: str, finding: str) -> None:
    found = unsafe_text({"data": {"notes": [text]}}, REPO_ROOT)
    assert any(item.startswith(finding) for item in found), found


def test_the_repository_path_and_keys_are_searched_too() -> None:
    assert unsafe_text({"note": f"in {REPO_ROOT.resolve()}"}, REPO_ROOT)
    assert unsafe_text({"api_key": 1}, REPO_ROOT)
    assert unsafe_text({"data": [1, 2.5, None, True, "requests/day"]}, REPO_ROOT) == []


def test_times_ratios_and_labels_are_no_address_or_path() -> None:
    for text in ("12:30:00", "1:2:3", "Model::fit", "H1: not significant", "MAE 1:30 days", "::"):
        assert unsafe_text({"note": text}, REPO_ROOT) == [], text


@pytest.mark.parametrize("flag", ["point_forecast_validated", "validated_recommendation"])
def test_the_validation_flags_are_read_from_the_answers(flag: str) -> None:
    """Each answer's own flag, not a constant: one answer claiming it makes it true."""
    by_file = {name: _answer(name).document() for name in _manifest()["files"]}
    status = static_export._scientific_status(by_file)
    assert status["point_forecast_validated"] is False
    assert status["validated_recommendation"] is False
    by_file["forecast.json"]["integrity"][flag] = True
    status = static_export._scientific_status(by_file)
    assert status[flag] is True
    other = ({"point_forecast_validated", "validated_recommendation"} - {flag}).pop()
    assert status[other] is False


@pytest.mark.parametrize(
    ("file", "change", "problem"),
    [
        (
            "models.json",
            lambda d: d["integrity"].update(validated_recommendation=True),
            "integrity.validated_recommendation is not false",
        ),
        (
            "forecast.json",
            lambda d: d["integrity"].update(point_forecast_validated=True),
            "integrity.point_forecast_validated is not false",
        ),
        (
            "intervals.json",
            lambda d: d["integrity"].update(uncertainty_interval_status="decision_grade"),
            "its M3 interval status differs",
        ),
        ("models.json", lambda d: d["integrity"].update(notices=[]), "it carries no notice"),
        (
            "scenario-normal.json",
            lambda d: d["integrity"].update(parameter_provenance=None),
            "an M4 answer that is not labelled HYPOTHETICAL",
        ),
        (
            "models.json",
            lambda d: d["provenance"].update(atlas_version="0.0.1"),
            "its ATLAS version is not this package's",
        ),
        (
            "models.json",
            lambda d: d["provenance"]["sources"][0].update(path="/opt/atlas/docs/results"),
            "is not repository-relative",
        ),
        (
            "models.json",
            lambda d: d["provenance"]["sources"][0].update(path="docs/../../etc"),
            "is not repository-relative",
        ),
        (
            "models.json",
            lambda d: d["provenance"]["sources"][0].update(files={"metrics.json": "0" * 64}),
            "differs from the SHA-256 named",
        ),
        (
            "models.json",
            lambda d: d["provenance"]["sources"][0].update(files={"metrics.json": "short"}),
            "has no SHA-256",
        ),
        (
            "models.json",
            lambda d: d["provenance"]["sources"][0].update(files={"absent.json": "0" * 64}),
            "is not a file of the repository",
        ),
        (
            "models.json",
            lambda d: d["data"].update(note="computed on 192.168.1.20"),
            "unsafe to publish, an IP address",
        ),
        ("models.json", lambda d: d.pop("provenance"), "no provenance block"),
    ],
)
def test_an_answer_that_cannot_be_published_is_refused(
    file: str, change: Any, problem: str
) -> None:
    problems = check_answer(_with(_answer(file), change), REPO_ROOT, "failed_calibration")
    assert any(problem in p for p in problems), problems
    assert check_answer(_answer(file), REPO_ROOT, "failed_calibration") == []


def test_an_answer_must_be_a_json_object() -> None:
    with pytest.raises(StaticExportError, match="not a JSON object"):
        Answer("x.json", "/api/v1/x", b"[1]").document()


# --- Nothing written when something is wrong ------------------------------------------


def test_nothing_is_exported_from_damaged_research_outputs(repo_copy: Path) -> None:
    root = add_api_inputs(repo_copy)
    plans = root / "docs" / "results" / "atlas-m4-optimization-v1" / "plans.csv"
    plans.write_bytes(plans.read_bytes() + b"\n")
    output = root / "web" / "static-export"
    with pytest.raises(StaticExportError, match="did not pass the API's checks"):
        write_static_export(root, SNAPSHOT, output)
    assert not output.exists()
    # Through the command: a failed check (exit 1) that names the reason.
    lines: list[str] = []

    class Out:
        def write(self, text: str) -> None:
            lines.append(text)

    code = main(["--repo-root", str(root), "static", "export"], stdout=Out())  # type: ignore[arg-type]
    assert code == 1
    assert "static_export_refused" in "".join(lines)
    assert "m4_reference_run" in "".join(lines)
    assert not output.exists()


def test_a_request_the_api_refuses_stops_the_export(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = Recorder(export_settings(REPO_ROOT, SNAPSHOT))
    try:
        with pytest.raises(StaticExportError, match="answered HTTP 404"):
            recorder.get("scenario-none.json", "/api/v1/scenarios/no_such_scenario")
        with pytest.raises(StaticExportError, match="answered HTTP 422"):
            recorder.get("history-x.json", "/api/v1/forecasts/history?start=1999-01-01")
        with pytest.raises(StaticExportError, match="not a valid export file name"):
            recorder.get("../models.json", "/api/v1/models")

        async def html(app: object, request: str) -> tuple[int, dict[str, str], bytes]:
            return 200, {"content-type": "text/html"}, b"<p>not JSON</p>"

        monkeypatch.setattr(static_export, "_asgi_get", html)
        with pytest.raises(StaticExportError, match="did not answer JSON"):
            recorder.get("models.json", "/api/v1/models")
    finally:
        recorder.close()


def test_the_settings_ignore_the_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fresh: Path
) -> None:
    monkeypatch.setenv("ATLAS_API_KEY", "k" * 32)
    monkeypatch.setenv("ATLAS_API_DOCS", "true")
    monkeypatch.setenv("ATLAS_API_SIMULATION_TIMEOUT_SECONDS", "99")
    monkeypatch.setenv("ATLAS_API_MAX_CONCURRENT_SIMULATIONS", "7")
    settings = export_settings(REPO_ROOT, SNAPSHOT)
    assert settings.api_key is None
    assert not settings.docs_enabled
    assert not settings.dashboard_enabled
    output = tmp_path / "export"
    write_static_export(REPO_ROOT, SNAPSHOT, output)
    for path in fresh.iterdir():
        assert (output / path.name).read_bytes() == path.read_bytes(), path.name


# --- Writing and verifying -------------------------------------------------------------


def test_an_earlier_export_is_replaced_and_anything_else_refused(
    tmp_path: Path, fresh: Path
) -> None:
    earlier = tmp_path / "earlier"
    shutil.copytree(fresh, earlier)
    (earlier / "stale-scenario.json").write_text("{}", encoding="utf-8")
    write_static_export(REPO_ROOT, SNAPSHOT, earlier)
    assert not (earlier / "stale-scenario.json").exists()
    assert sorted(p.name for p in earlier.iterdir()) == sorted(p.name for p in fresh.iterdir())

    strange = tmp_path / "strange"
    shutil.copytree(fresh, strange)
    (strange / "notes.txt").write_text("mine", encoding="utf-8")
    with pytest.raises(StaticExportError, match="holds files an export never writes"):
        write_static_export(REPO_ROOT, SNAPSHOT, strange)
    assert (strange / "notes.txt").exists()

    other = tmp_path / "other"
    other.mkdir()
    (other / "data.json").write_text("{}", encoding="utf-8")
    with pytest.raises(StaticExportError, match=r"holds no atlas-static-export\.json"):
        write_static_export(REPO_ROOT, SNAPSHOT, other)

    a_file = tmp_path / "a-file"
    a_file.write_text("x", encoding="utf-8")
    with pytest.raises(StaticExportError, match="is not a directory"):
        write_static_export(REPO_ROOT, SNAPSHOT, a_file)


def _issues(directory: Path) -> set[str]:
    return {i.code for r in verify_static_export(REPO_ROOT, SNAPSHOT, directory) for i in r.issues}


def test_verification_finds_every_difference(tmp_path: Path, fresh: Path) -> None:
    def copy(name: str) -> Path:
        target = tmp_path / name
        shutil.copytree(fresh, target)
        return target

    altered = copy("altered")
    (altered / "models.json").write_bytes(b"{}")
    assert {"static_export_altered", "static_export_differs"} <= _issues(altered)

    missing = copy("missing")
    (missing / "scenario-normal.json").unlink()
    assert {"static_export_missing", "static_export_differs"} <= _issues(missing)

    unlisted = copy("unlisted")
    (unlisted / "extra.json").write_text("{}", encoding="utf-8")
    assert "static_export_unlisted" in _issues(unlisted)

    # Consistent with its own manifest, but not what the committed outputs give.
    consistent = copy("consistent")
    body = (consistent / "models.json").read_bytes().replace(b"atlas_linear", b"atlas_LINEAR")
    (consistent / "models.json").write_bytes(body)
    manifest = _manifest(consistent)
    manifest["files"]["models.json"]["sha256"] = hashlib.sha256(body).hexdigest()
    (consistent / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2) + "\n")
    assert _issues(consistent) == {"static_export_differs", "static_export_manifest_differs"}

    extra = copy("extra")
    manifest = _manifest(extra)
    manifest["files"]["gone.json"] = {"request": "/api/v1/gone", "sha256": "0" * 64, "bytes": 0}
    (extra / MANIFEST_NAME).write_text(json.dumps(manifest), encoding="utf-8")
    assert {"static_export_extra", "static_export_missing"} <= _issues(extra)

    broken = copy("broken")
    (broken / MANIFEST_NAME).write_text("{", encoding="utf-8")
    assert _issues(broken) == {"static_export_manifest"}
    (broken / MANIFEST_NAME).write_text('{"format": "other/1"}', encoding="utf-8")
    assert _issues(broken) == {"static_export_manifest"}


def test_verification_reports_an_export_that_cannot_be_made(repo_copy: Path, fresh: Path) -> None:
    root = add_api_inputs(repo_copy)
    status = (
        root / "docs" / "results" / "atlas-m3-intervals-v1" / "full" / "calibration_status.json"
    )
    status.write_bytes(status.read_bytes().replace(b"failed_calibration", b"decision_grade"))
    reports = verify_static_export(root, SNAPSHOT, fresh)
    assert reports[0].ok
    assert {i.code for i in reports[1].issues} == {"static_export_refused"}


def test_the_command_exports_and_verifies(tmp_path: Path) -> None:
    output = tmp_path / "export"
    assert main(["static", "export", "--output-dir", str(output)]) == 0
    assert main(["static", "verify", str(output)]) == 0
    assert main(["--format", "json", "static", "verify", str(output)]) == 0
    (output / "forecast.json").write_bytes(b"{}")
    assert main(["static", "verify", str(output)]) == 1


def test_the_default_paths_are_the_committed_export(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--repo-root", str(REPO_ROOT), "static", "verify"]) == 0
    assert "static export reproduction" in capsys.readouterr().out
    assert DEFAULT_OUTPUT.as_posix() == "web/static-export"


# --- The static build's policy and the repository's rules ------------------------------


def test_the_static_build_carries_the_dashboard_policy_less_frame_ancestors() -> None:
    """A policy in a <meta> cannot carry frame-ancestors (GitHub Pages sends no header)."""
    source = (REPO_ROOT / "web" / "build" / "static-site.ts").read_text(encoding="utf-8")
    match = re.search(r"export const STATIC_CSP =\s*((?:\s*\"[^\"]*\"\s*\+?)+);", source)
    assert match is not None
    policy = "".join(re.findall(r"\"([^\"]*)\"", match.group(1)))
    assert policy == DASHBOARD_CSP.replace("frame-ancestors 'none'; ", "")


def test_the_export_is_protected_from_line_ending_conversion() -> None:
    attributes = (REPO_ROOT / ".gitattributes").read_text(encoding="utf-8").splitlines()
    assert "web/static-export/** -text" in attributes


def test_the_module_states_what_it_leaves_out() -> None:
    assert next(r for r, _ in static_export.NOT_EXPORTED) == "GET /api/v1/system"
