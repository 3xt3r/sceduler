from __future__ import annotations

import argparse
import base64
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

def resolve_path(value: str, base_dir: Path) -> Path:
    """
    Absolute paths stay absolute.
    Relative paths are resolved relative to config.yml directory.
    """
    p = Path(value).expanduser()

    if p.is_absolute():
        return p

    return (base_dir / p).resolve()


def load_config(path: Path) -> Config:
    path = path.resolve()
    base_dir = path.parent

    raw = yaml.safe_load(path.read_text(encoding="utf-8"))

    if not isinstance(raw, dict):
        raise ValueError(f"config is empty or invalid: {path}")

    products: list[ProductSpec] = []

    for p in raw.get("products") or []:
        versions: list[VersionSpec] = []

        for v in p.get("versions") or []:
            repos = [
                RepoSpec(
                    url=str(r["url"]),
                    branch=str(r["branch"]),
                    dir=str(r["dir"]),
                )
                for r in v.get("repos") or []
            ]

            versions.append(
                VersionSpec(
                    version=str(v["version"]),
                    repos=repos,
                )
            )

        products.append(
            ProductSpec(
                name=str(p["name"]),
                versions=versions,
            )
        )

    return Config(
        scanner_py=resolve_path(str(raw["scanner_py"]), base_dir),
        work_dir=resolve_path(str(raw["work_dir"]), base_dir),
        results_dir=resolve_path(str(raw["results_dir"]), base_dir),
        env_file=resolve_path(str(raw["env_file"]), base_dir),
        products=products,
    )


def load_gitlab_token(env_file: Path) -> str:
    """
    Reads GITLAB_TOKEN from .env.
    If .env does not exist or token is not found there,
    falls back to environment variable GITLAB_TOKEN.
    """
    if not env_file.is_file():
        log.warning(".env file not found: %s", env_file)
        return os.environ.get("GITLAB_TOKEN", "").strip()

    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()

        if not line or line.startswith("#"):
            continue

        if line.startswith("GITLAB_TOKEN="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")

    return os.environ.get("GITLAB_TOKEN", "").strip()


# ---------------------------------------------------------------------------
# Safe Git auth
# ---------------------------------------------------------------------------

def git_auth_config_args(token: str) -> list[str]:
    """
    Adds HTTP Basic auth header for GitLab HTTPS operations.

    This avoids putting token directly into repo URL.
    Token will not be stored in .git/config.
    Token will not be printed in normal logs.

    Equivalent GitLab HTTPS auth:
        username: oauth2
        password: <GITLAB_TOKEN>
    """
    if not token:
        return []

    raw = f"oauth2:{token}".encode("utf-8")
    encoded = base64.b64encode(raw).decode("ascii")

    return [
        "-c",
        f"http.extraHeader=Authorization: Basic {encoded}",
    ]


def mask_sensitive_arg(arg: object) -> str:
    s = str(arg)

    if s.startswith("http.extraHeader=Authorization: Basic "):
        return "http.extraHeader=Authorization: Basic ***"

    if "oauth2:" in s and "@" in s:
        before, after = s.split("oauth2:", 1)
        if "@" in after:
            _, rest = after.split("@", 1)
            return before + "oauth2:***@" + rest

    return s


def safe_cmd_for_log(args: list[object]) -> str:
    return " ".join(mask_sensitive_arg(a) for a in args)


# ---------------------------------------------------------------------------
# Command helpers
# ---------------------------------------------------------------------------

def run_cmd(
    args: list[str],
    cwd: Path | None = None,
    label: str = "",
    timeout: int = 600,
) -> tuple[bool, str]:
    display = label or " ".join(str(a) for a in args[:4])

    log.info("    running: %s", safe_cmd_for_log(args))

    env = os.environ.copy()

    # Disable interactive Git password prompt.
    # If token is wrong, Git fails instead of hanging.
    env["GIT_TERMINAL_PROMPT"] = "0"

    try:
        result = subprocess.run(
            args,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )

        if result.returncode != 0:
            msg = (result.stderr or result.stdout or "").strip()
            log.error("    [FAIL] %s: %s", display, msg)
            return False, msg

        return True, ""

    except subprocess.TimeoutExpired:
        msg = f"timeout after {timeout}s: {display}"
        log.error("    [FAIL] %s", msg)
        return False, msg

    except Exception as exc:
        log.error("    [FAIL] %s: %s", display, exc)
        return False, str(exc)


def run_git(
    git_args: list[str],
    cwd: Path | None = None,
    token: str = "",
    label: str = "",
) -> tuple[bool, str]:
    args = ["git", *git_auth_config_args(token), *git_args]
    return run_cmd(args, cwd=cwd, label=label)


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------

def ensure_clone(repo_dir: Path, clean_url: str, token: str) -> tuple[bool, str]:
    """
    Clone repo if not present.
    If present, ensure origin URL is clean and fetch all branches.

    Shared clones live in:
        work_dir/<product>/_repos/<repo.dir>/

    Example:
        /home/user/jobs/GF/_repos/backend
        /home/user/jobs/GF/_repos/frontend
        /home/user/jobs/GF/_repos/libs

    These clones are reused between versions.
    """
    if (repo_dir / ".git").exists():
        log.info("    repo already cloned: %s — fetching", repo_dir.name)

        ok, err = run_git(
            ["remote", "set-url", "origin", clean_url],
            cwd=repo_dir,
            token=token,
            label="git remote set-url",
        )

        if not ok:
            return False, err

        return run_git(
            ["fetch", "--prune", "--all"],
            cwd=repo_dir,
            token=token,
            label="git fetch",
        )

    log.info("    cloning: %s", clean_url)

    repo_dir.parent.mkdir(parents=True, exist_ok=True)

    return run_git(
        ["clone", "--no-checkout", clean_url, str(repo_dir)],
        token=token,
        label="git clone",
    )


def checkout_branch(repo_dir: Path, branch: str, token: str) -> tuple[bool, str]:
    """
    Switch shared clone to required branch.
    """
    log.info("    checkout %s @ %s", repo_dir.name, branch)

    ok, err = run_git(
        ["checkout", branch],
        cwd=repo_dir,
        token=token,
        label=f"git checkout {branch}",
    )

    if not ok:
        return False, err

    ok, err = run_git(
        ["reset", "--hard", f"origin/{branch}"],
        cwd=repo_dir,
        token=token,
        label=f"git reset origin/{branch}",
    )

    if not ok:
        return False, err

    # Submodules are non-fatal.
    # If they fail, repo is still used.
    ok, err = run_git(
        ["submodule", "update", "--init", "--recursive"],
        cwd=repo_dir,
        token=token,
        label="git submodule update",
    )

    if not ok:
        log.warning("    submodule update failed for %s: %s", repo_dir.name, err)

    return True, ""


# ---------------------------------------------------------------------------
# Scan workspace
# ---------------------------------------------------------------------------

def build_scan_workspace(
    sources_dir: Path,
    version: VersionSpec,
    repos_dir: Path,
    token: str,
) -> list[str]:
    """
    For each repo in selected version:
      1. Ensure shared clone exists in repos_dir/<dir>/
      2. git fetch + checkout required branch
      3. Copy repo into sources_dir/<dir>/ as clean scan workspace

    Shared clones are kept between runs.

    Scan workspace is rebuilt fresh every time.

    Example:
        /home/user/jobs/GF/_repos/backend  ->  /home/user/jobs/GF/sources/backend

    Returns list of skipped repos with error messages.
    """
    skipped: list[str] = []

    if sources_dir.exists():
        log.info("  removing previous sources workspace: %s", sources_dir)
        shutil.rmtree(sources_dir)

    sources_dir.mkdir(parents=True, exist_ok=True)

    for repo in version.repos:
        shared_repo_dir = repos_dir / repo.dir

        ok, err = ensure_clone(
            repo_dir=shared_repo_dir,
            clean_url=repo.url,
            token=token,
        )

        if not ok:
            log.error("    skipping %s because clone/fetch failed: %s", repo.dir, err)
            skipped.append(f"{repo.dir}: clone/fetch failed: {err}")
            continue

        ok, err = checkout_branch(
            repo_dir=shared_repo_dir,
            branch=repo.branch,
            token=token,
        )

        if not ok:
            log.error(
                "    skipping %s because checkout %s failed: %s",
                repo.dir,
                repo.branch,
                err,
            )
            skipped.append(f"{repo.dir}: checkout {repo.branch} failed: {err}")
            continue

        dest = sources_dir / repo.dir

        log.info("    copying %s -> %s", shared_repo_dir.name, dest)

        if dest.exists():
            shutil.rmtree(dest)

        shutil.copytree(
            shared_repo_dir,
            dest,
            symlinks=True,
        )

    return skipped


def cleanup_scan_workspace(sources_dir: Path) -> None:
    """
    Remove temporary sources workspace after results are collected.
    """
    if sources_dir.exists():
        log.info("  cleaning sources workspace: %s", sources_dir)

        try:
            shutil.rmtree(sources_dir)
        except Exception as exc:
            log.warning("  failed to clean workspace %s: %s", sources_dir, exc)


# ---------------------------------------------------------------------------
# Scanner + result collection
# ---------------------------------------------------------------------------

def run_scanner(scanner_py: Path, scan_root: Path, env_file: Path) -> tuple[bool, str]:
    """
    Runs scanner.py on prepared sources directory.

    Equivalent manual command:

        cd /home/user/oss_checks
        python scanner.py /home/user/jobs/GF/sources --apply --deptrack --env-file .env

    But scheduler uses absolute paths.
    """
    if not scanner_py.is_file():
        return False, f"scanner.py not found: {scanner_py}"

    if not scan_root.is_dir():
        return False, f"scan root not found: {scan_root}"

    if not env_file.is_file():
        return False, f".env file not found: {env_file}"

    log.info("  running scanner on %s", scan_root)

    return run_cmd(
        [
            sys.executable,
            str(scanner_py),
            str(scan_root),
            "--apply",
            "--deptrack",
            "--env-file",
            str(env_file),
        ],
        cwd=scanner_py.parent,
        label="scanner.py",
        timeout=3600,
    )


def find_scanner_job_dir(sources_dir: Path) -> Path | None:
    """
    Scanner writes to:
        <scan_root.parent>/jobs/<run_id>/

    If scan_root is:
        /home/user/jobs/GF/sources

    Then jobs are expected here:
        /home/user/jobs/GF/jobs/<run_id>/

    Returns most recently modified job dir.
    """
    jobs_root = sources_dir.parent / "jobs"

    if not jobs_root.is_dir():
        return None

    candidates = sorted(
        (d for d in jobs_root.iterdir() if d.is_dir()),
        key=lambda d: d.stat().st_mtime,
        reverse=True,
    )

    return candidates[0] if candidates else None


def collect_results(sources_dir: Path, result_dir: Path) -> bool:
    """
    Move scanner job output into final result_dir.

    Collects partial output on scanner failure too,
    as long as scanner created a job directory.
    """
    job_dir = find_scanner_job_dir(sources_dir)

    if not job_dir:
        log.error("  could not find scanner job dir under %s", sources_dir.parent / "jobs")
        return False

    result_dir.mkdir(parents=True, exist_ok=True)

    log.info("  moving results: %s -> %s", job_dir, result_dir)

    try:
        for item in job_dir.iterdir():
            dest = result_dir / item.name

            if dest.exists():
                if dest.is_dir():
                    shutil.rmtree(dest)
                else:
                    dest.unlink()

            shutil.move(str(item), str(dest))

        job_dir.rmdir()

        jobs_root = sources_dir.parent / "jobs"

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
    lines = [
        f"Scan run: {datetime.now().isoformat()}",
        "",
    ]

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

    config.work_dir.mkdir(parents=True, exist_ok=True)
    config.results_dir.mkdir(parents=True, exist_ok=True)

    for product in config.products:
        log.info("=== product: %s ===", product.name)

        # Shared clones.
        # Kept between runs.
        #
        # Example:
        #   /home/user/jobs/GF/_repos/backend
        #   /home/user/jobs/GF/_repos/frontend
        #   /home/user/jobs/GF/_repos/libs
        repos_dir = config.work_dir / product.name / "_repos"
        repos_dir.mkdir(parents=True, exist_ok=True)

        # Temporary scan workspace.
        # Rebuilt per version.
        #
        # Example:
        #   /home/user/jobs/GF/sources
        #
        # This replaces your manual command argument:
        #   python scanner.py sources ...
        sources_dir = config.work_dir / product.name / "sources"

        for version in product.versions:
            label = f"{product.name} / {version.version}"

            log.info("--- version: %s ---", version.version)

            skipped_repos = build_scan_workspace(
                sources_dir=sources_dir,
                version=version,
                repos_dir=repos_dir,
                token=token,
            )

            result_dir = (
                config.results_dir
                / run_date
                / f"{product.name}__{version.version}"
            )

            ok, err = run_scanner(
                scanner_py=config.scanner_py,
                scan_root=sources_dir,
                env_file=config.env_file,
            )

            collected = collect_results(
                sources_dir=sources_dir,
                result_dir=result_dir,
            )

            if not collected:
                log.warning("  could not collect results for %s", label)

            cleanup_scan_workspace(sources_dir)

            success = ok and collected

            results.append(
                RunResult(
                    product=product.name,
                    version=version.version,
                    success=success,
                    error=err,
                    result_dir=result_dir if collected else None,
                    skipped_repos=skipped_repos,
                )
            )

            if success:
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

    parser = argparse.ArgumentParser(
        description="Run OSS scanner for multiple products and versions."
    )

    parser.add_argument(
        "--config",
        default=str(Path(__file__).parent / "config.yml"),
        help="Path to config.yml. Default: config.yml near scheduler.py",
    )

    args = parser.parse_args()

    config_path = Path(args.config).expanduser().resolve()

    if not config_path.is_file():
        log.error("config.yml not found: %s", config_path)
        return 1

    try:
        config = load_config(config_path)
    except Exception as exc:
        log.error("failed to load config: %s", exc)
        return 1

    results = run_all(config)

    run_log = (
        config.results_dir
        / datetime.now().strftime("%Y-%m-%d")
        / "run.log"
    )

    write_run_log(run_log, results)

    failed = [r for r in results if not r.success]

    if failed:
        log.error("%d/%d scan(s) failed", len(failed), len(results))
        return 1

    log.info("all %d scan(s) completed successfully", len(results))
    return 0


if __name__ == "__main__":
    sys.exit(main())
