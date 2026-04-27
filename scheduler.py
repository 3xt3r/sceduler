from __future__ import annotations

import logging
import os
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import yaml

log = logging.getLogger(__name__)


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


def run_cmd(args: list[str], cwd: Path | None = None, label: str = "") -> tuple[bool, str]:
    display = label or " ".join(args[:3])
    log.info("  running: %s", " ".join(args))
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
            log.error("  [FAIL] %s: %s", display, msg)
            return False, msg
        return True, ""
    except subprocess.TimeoutExpired:
        msg = f"timeout after 600s: {display}"
        log.error("  [FAIL] %s", msg)
        return False, msg
    except Exception as exc:
        log.error("  [FAIL] %s: %s", display, exc)
        return False, str(exc)


def sync_repo(repo_dir: Path, url_with_token: str, branch: str) -> tuple[bool, str]:
    if (repo_dir / ".git").exists():
        log.info("  pulling %s @ %s", repo_dir.name, branch)
        ok, err = run_cmd(["git", "fetch", "--prune"], cwd=repo_dir)
        if not ok:
            return False, err
        ok, err = run_cmd(["git", "checkout", branch], cwd=repo_dir)
        if not ok:
            return False, err
        ok, err = run_cmd(["git", "reset", "--hard", f"origin/{branch}"], cwd=repo_dir)
        if not ok:
            return False, err
    else:
        log.info("  cloning into %s @ %s", repo_dir, branch)
        repo_dir.mkdir(parents=True, exist_ok=True)
        ok, err = run_cmd(
            ["git", "clone", "--branch", branch, "--single-branch", url_with_token, str(repo_dir)],
            label=f"git clone {branch}",
        )
        if not ok:
            return False, err

    ok, err = run_cmd(
        ["git", "submodule", "update", "--init", "--recursive"],
        cwd=repo_dir,
    )
    if not ok:
        log.warning("  submodule update failed for %s: %s", repo_dir.name, err)

    return True, ""


def prepare_version(
    version_dir: Path,
    version: VersionSpec,
    token: str,
) -> list[str]:
    skipped: list[str] = []
    for repo in version.repos:
        repo_dir = version_dir / repo.dir
        url = inject_token(repo.url, token)
        ok, err = sync_repo(repo_dir, url, repo.branch)
        if not ok:
            log.error("    skipping repo %s: %s", repo.dir, err)
            skipped.append(f"{repo.dir}: {err}")
    return skipped


def run_scanner(
    scanner_py: Path,
    scan_root: Path,
    result_dir: Path,
    env_file: Path,
) -> tuple[bool, str]:
    result_dir.mkdir(parents=True, exist_ok=True)
    log.info("  running scanner on %s", scan_root)
    ok, err = run_cmd(
        [
            sys.executable,
            str(scanner_py),
            str(scan_root),
            "--apply",
            "--env-file", str(env_file),
        ],
        label="scanner.py",
    )
    return ok, err


def write_run_log(log_path: Path, results: list[RunResult]) -> None:
    lines = [f"Scan run: {datetime.now().isoformat()}", ""]
    for r in results:
        status = "OK" if r.success else "FAIL"
        lines.append(f"[{status}] {r.product} / {r.version}")
        if r.result_dir:
            lines.append(f"       results: {r.result_dir}")
        if r.error:
            lines.append(f"       error:   {r.error}")
        if r.skipped_repos:
            for s in r.skipped_repos:
                lines.append(f"       skipped repo: {s}")
        lines.append("")
    log_path.write_text("\n".join(lines), encoding="utf-8")
    log.info("run log written: %s", log_path)


def run_all(config: Config) -> list[RunResult]:
    token = load_gitlab_token(config.env_file)
    if not token:
        log.warning("GITLAB_TOKEN not set — cloning may fail for private repos")

    run_date = datetime.now().strftime("%Y-%m-%d")
    results: list[RunResult] = []

    for product in config.products:
        log.info("=== product: %s ===", product.name)
        for version in product.versions:
            label = f"{product.name} / {version.version}"
            log.info("--- version: %s ---", version.version)

            version_dir = config.work_dir / product.name / version.version
            version_dir.mkdir(parents=True, exist_ok=True)

            skipped_repos = prepare_version(version_dir, version, token)

            result_dir = config.results_dir / run_date / f"{product.name}__{version.version}"
            result_dir.mkdir(parents=True, exist_ok=True)

            ok, err = run_scanner(
                scanner_py=config.scanner_py,
                scan_root=version_dir,
                result_dir=result_dir,
                env_file=config.env_file,
            )

            results.append(RunResult(
                product=product.name,
                version=version.version,
                success=ok,
                error=err,
                result_dir=result_dir,
                skipped_repos=skipped_repos,
            ))

            if ok:
                log.info("  [OK] %s", label)
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
