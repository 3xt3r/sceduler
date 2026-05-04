from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import yaml

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config dataclasses
# ---------------------------------------------------------------------------

@dataclass
class RepoSpec:
    url: str
    branch: str
    dir: str


@dataclass
class VersionSpec:
    version: str
    repos: list[RepoSpec]


@dataclass
class ProductSpec:
    name: str
    versions: list[VersionSpec]


@dataclass
class Config:
    scanner_py: Path
    work_dir: Path
    results_dir: Path
    env_file: Path
    products: list[ProductSpec]


@dataclass
class RunResult:
    product: str
    version: str
    success: bool
    error: str = ""
    result_dir: Path | None = None
    skipped_repos: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

def load_config(path: Path) -> Config:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    products = []
    for p in raw.get("products") or []:
        versions = []
        for v in p.get("versions") or []:
            repos = [
                RepoSpec(url=r["url"], branch=r["branch"], dir=r["dir"])
                for r in v.get("repos") or []
            ]
            versions.append(VersionSpec(version=str(v["version"]), repos=repos))
        products.append(ProductSpec(name=p["name"], versions=versions))
    return Config(
        scanner_py=Path(raw["scanner_py"]),
        work_dir=Path(raw["work_dir"]),
        results_dir=Path(raw["results_dir"]),
        env_file=Path(raw["env_file"]),
        products=products,
    )


def load_gitlab_token(env_file: Path) -> str:
    if not env_file.is_file():
        log.warning(".env file not found: %s", env_file)
        return os.environ.get("GITLAB_TOKEN", "")
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("GITLAB_TOKEN="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return os.environ.get("GITLAB_TOKEN", "")


def inject_token(url: str, token: str) -> str:
    if not token:
        return url
    if url.startswith("https://"):
        return url.replace("https://", f"https://oauth2:{token}@", 1)
    return url


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------

def run_cmd(args: list[str], cwd: Path | None = None, label: str = "") -> tuple[bool, str]:
    display = label or " ".join(str(a) for a in args[:4])
    log.info("    running: %s", " ".join(str(a) for a in args))
    try:
        result = subprocess.run(
            args,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=600,
        )
        if result.returncode != 0:
            msg = (result.stderr or result.stdout or "").strip()
            log.error("    [FAIL] %s: %s", display, msg)
            return False, msg
        return True, ""
    except subprocess.TimeoutExpired:
        msg = f"timeout after 600s: {display}"
        log.error("    [FAIL] %s", msg)
        return False, msg
    except Exception as exc:
        log.error("    [FAIL] %s: %s", display, exc)
        return False, str(exc)


def ensure_clone(repo_dir: Path, url_with_token: str) -> tuple[bool, str]:
    """Clone repo if not yet present, otherwise fetch all branches."""
    if (repo_dir / ".git").exists():
        log.info("    repo already cloned: %s — fetching", repo_dir.name)
        return run_cmd(["git", "fetch", "--prune", "--all"], cwd=repo_dir)

    log.info("    cloning: %s", url_with_token.split("@")[-1])
    repo_dir.mkdir(parents=True, exist_ok=True)
    return run_cmd(
        ["git", "clone", "--no-checkout", url_with_token, str(repo_dir)],
        label="git clone",
    )


def checkout_branch(repo_dir: Path, branch: str) -> tuple[bool, str]:
    """Switch shared clone to the required branch."""
    log.info("    checkout %s @ %s", repo_dir.name, branch)
    ok, err = run_cmd(["git", "checkout", branch], cwd=repo_dir)
    if not ok:
        return False, err
    ok, err = run_cmd(["git", "reset", "--hard", f"origin/{branch}"], cwd=repo_dir)
    if not ok:
        return False, err
    # Update submodules — non-fatal if it fails
    ok, err = run_cmd(
        ["git", "submodule", "update", "--init", "--recursive"],
        cwd=repo_dir,
    )
    if not ok:
        log.warning("    submodule update failed for %s: %s", repo_dir.name, err)
    return True, ""


# ---------------------------------------------------------------------------
# Scan workspace
# ---------------------------------------------------------------------------

def build_scan_workspace(
    version_dir: Path,
    version: VersionSpec,
    repos_dir: Path,
    token: str,
) -> list[str]:
    """
    For each repo in the version:
      1. Ensure the shared clone exists in repos_dir/<dir>/
      2. git fetch + checkout required branch
      3. Copy into version_dir/<dir>/ as a clean scan workspace

    Shared clones in repos_dir are kept between runs.
    The scan workspace (version_dir) is rebuilt fresh every time.
    Returns list of skipped repos with error messages.
    """
    skipped: list[str] = []

    if version_dir.exists():
        shutil.rmtree(version_dir)
    version_dir.mkdir(parents=True, exist_ok=True)

    for repo in version.repos:
        shared_repo_dir = repos_dir / repo.dir
        url = inject_token(repo.url, token)

        ok, err = ensure_clone(shared_repo_dir, url)
        if not ok:
            log.error("    skipping %s (clone/fetch failed): %s", repo.dir, err)
            skipped.append(f"{repo.dir}: clone/fetch failed: {err}")
            continue

        ok, err = checkout_branch(shared_repo_dir, repo.branch)
        if not ok:
            log.error("    skipping %s (checkout %s failed): %s", repo.dir, repo.branch, err)
            skipped.append(f"{repo.dir}: checkout {repo.branch} failed: {err}")
            continue

        dest = version_dir / repo.dir
        log.info("    copying %s -> %s", shared_repo_dir.name, dest)
        shutil.copytree(shared_repo_dir, dest, symlinks=True)

    return skipped


def cleanup_scan_workspace(version_dir: Path) -> None:
    """Remove temporary scan workspace after results are collected."""
    if version_dir.exists():
        log.info("  cleaning scan workspace: %s", version_dir)
        try:
            shutil.rmtree(version_dir)
        except Exception as exc:
            log.warning("  failed to clean workspace %s: %s", version_dir, exc)


# ---------------------------------------------------------------------------
# Scanner + result collection
# ---------------------------------------------------------------------------

def run_scanner(scanner_py: Path, scan_root: Path, env_file: Path) -> tuple[bool, str]:
    log.info("  running scanner on %s", scan_root)
    return run_cmd(
        [sys.executable, str(scanner_py), str(scan_root), "--apply", "--env-file", str(env_file)],
        label="scanner.py",
    )


def find_scanner_job_dir(version_dir: Path) -> Path | None:
    """
    Scanner writes to <scan_root.parent>/jobs/<run_id>/.
    scan_root = version_dir  =>  jobs land in version_dir.parent/jobs/.
    Returns the most recently modified job dir.
    """
    jobs_root = version_dir.parent / "jobs"
    if not jobs_root.is_dir():
        return None
    candidates = sorted(
        (d for d in jobs_root.iterdir() if d.is_dir()),
        key=lambda d: d.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def collect_results(version_dir: Path, result_dir: Path) -> bool:
    """Move scanner job output into result_dir. Collects partial output on failure too."""
    job_dir = find_scanner_job_dir(version_dir)
    if not job_dir:
        log.error("  could not find scanner job dir under %s", version_dir.parent / "jobs")
        return False

    result_dir.mkdir(parents=True, exist_ok=True)
    log.info("  moving results: %s -> %s", job_dir, result_dir)
    try:
        for item in job_dir.iterdir():
            dest = result_dir / item.name
            if dest.exists():
                shutil.rmtree(dest) if dest.is_dir() else dest.unlink()
            shutil.move(str(item), str(dest))
        job_dir.rmdir()
        jobs_root = version_dir.parent / "jobs"
        if jobs_root.is_dir() and not any(jobs_root.iterdir()):
            jobs_root.rmdir()
        return True
    except Exception as exc:
        log.error("  failed to move results: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Run log
# ---------------------------------------------------------------------------

def write_run_log(log_path: Path, results: list[RunResult]) -> None:
    lines = [f"Scan run: {datetime.now().isoformat()}", ""]
    for r in results:
        status = "OK" if r.success else "FAIL"
        lines.append(f"[{status}] {r.product} / {r.version}")
        if r.result_dir:
            lines.append(f"       results: {r.result_dir}")
        if r.error:
            lines.append(f"       error:   {r.error}")
        for s in r.skipped_repos:
            lines.append(f"       skipped: {s}")
        lines.append("")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("\n".join(lines), encoding="utf-8")
    log.info("run log written: %s", log_path)


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def run_all(config: Config) -> list[RunResult]:
    token = load_gitlab_token(config.env_file)
    if not token:
        log.warning("GITLAB_TOKEN not set — cloning may fail for private repos")

    run_date = datetime.now().strftime("%Y-%m-%d")
    results: list[RunResult] = []

    for product in config.products:
        log.info("=== product: %s ===", product.name)

        # Shared clones — kept between runs, reused via checkout
        repos_dir = config.work_dir / product.name / "_repos"
        repos_dir.mkdir(parents=True, exist_ok=True)

        # Temporary scan workspace — rebuilt per version, deleted after
        version_dir = config.work_dir / product.name / "_scan"

        for version in product.versions:
            label = f"{product.name} / {version.version}"
            log.info("--- version: %s ---", version.version)

            skipped_repos = build_scan_workspace(version_dir, version, repos_dir, token)

            result_dir = config.results_dir / run_date / f"{product.name}__{version.version}"

            ok, err = run_scanner(
                scanner_py=config.scanner_py,
                scan_root=version_dir,
                env_file=config.env_file,
            )

            collected = collect_results(version_dir, result_dir)
            if not collected:
                log.warning("  could not collect results for %s", label)

            # transitive_libs were inside job_dir — already moved with results
            cleanup_scan_workspace(version_dir)

            results.append(RunResult(
                product=product.name,
                version=version.version,
                success=ok and collected,
                error=err,
                result_dir=result_dir if collected else None,
                skipped_repos=skipped_repos,
            ))

            if ok:
                log.info("  [OK] %s -> %s", label, result_dir)
            else:
                log.error("  [FAIL] %s: %s", label, err)

    return results


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-7s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    config_path = Path(__file__).parent / "config.yml"
    if not config_path.is_file():
        log.error("config.yml not found: %s", config_path)
        return 1

    try:
        config = load_config(config_path)
    except Exception as exc:
        log.error("failed to load config: %s", exc)
        return 1

    results = run_all(config)

    run_log = config.results_dir / datetime.now().strftime("%Y-%m-%d") / "run.log"
    write_run_log(run_log, results)

    failed = [r for r in results if not r.success]
    if failed:
        log.error("%d/%d scan(s) failed", len(failed), len(results))
        return 1

    log.info("all %d scan(s) completed successfully", len(results))
    return 0


if __name__ == "__main__":
    sys.exit(main())
