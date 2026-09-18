#!/usr/bin/env python3
"""Bounded OpenCode implementation with independently executed evidence gates."""

import argparse
import hashlib
import json
import os
import signal
import stat
import subprocess
import time
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

HOME = Path(__file__).resolve().parent


def write(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.replace(path)


def execute(argv, cwd, timeout=180, env=None):
    started = time.time()
    with subprocess.Popen(
        argv,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    ) as proc:
        try:
            output, _ = proc.communicate(timeout=timeout)
            code = proc.returncode
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            output, _ = proc.communicate()
            code = 124
    return dict(
        command=argv,
        exit_code=code,
        output=output.decode(errors="replace"),
        timestamp=started,
        duration_seconds=round(time.time() - started, 3),
    )


def snapshot(repo):
    names = execute(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], repo)
    if names["exit_code"]:
        raise ValueError("A Git repository is required")
    result = {}
    for name in sorted(set(names["output"].split("\0")) - {""}):
        path = repo / name
        if path.is_symlink():
            data = os.readlink(path).encode()
        elif path.is_file():
            data = path.read_bytes()
        else:
            data = b"<missing>"
        result[name] = hashlib.sha256(
            data + str(stat.S_IMODE(path.lstat().st_mode) if path.exists() else 0).encode()
        ).hexdigest()
    result["@HEAD"] = execute(["git", "rev-parse", "HEAD"], repo)["output"].strip()
    return result


def validate(c):
    for field in ["objective", "unchanged", "acceptance", "negative_cases", "files", "checks"]:
        if not c.get(field):
            raise ValueError("Contract requires nonempty " + field)
    for field in [
        "unchanged",
        "acceptance",
        "negative_cases",
        "files",
        "checks",
        "gates",
        "dependencies",
    ]:
        if not isinstance(c.get(field), list):
            raise ValueError(field + " must be a list")
    for name in c["files"]:
        if Path(name).is_absolute() or ".." in Path(name).parts or any(x in name for x in "*?["):
            raise ValueError("files must be exact relative paths")
        if name in [
            ".git",
            ".gitignore",
            "opencode.json",
            "opencode.jsonc",
            "AGENTS.md",
        ] or name.startswith((".git/", ".opencode/", ".omo/", ".harness/", "tools/dev_harness/")):
            raise ValueError("Harness/config edits require senior review")
    for check in c["checks"]:
        if (
            not isinstance(check.get("argv"), list)
            or not check["argv"]
            or not all(isinstance(x, str) for x in check["argv"])
        ):
            raise ValueError("checks require argv arrays; no implicit shell")
        if check.get("kind") not in ["pytest", "command"]:
            raise ValueError("check kind must be pytest or command")
    if c.get("risk", "normal") not in ["normal", "high", "security"]:
        raise ValueError("Invalid risk")


def model_text(result):
    texts = []
    for line in result["output"].splitlines():
        try:
            event = json.loads(line)
            if event.get("type") == "text":
                texts.append(event["part"]["text"])
        except (ValueError, KeyError, TypeError):
            pass
    return "\n".join(texts)


def agent(role, prompt, repo, c, routing):
    model = routing[role]
    if not model.startswith("opencode/") or not model.endswith("-free"):
        raise ValueError("Automatic roles require explicitly free models")
    permission = {
        "*": "deny",
        "read": {
            "*": "allow",
            "*.env*": "deny",
            "*credentials*": "deny",
            "*token.json": "deny",
            "*.pem": "deny",
            "*.key": "deny",
            "*lifeos_bootstrap*": "deny",
        },
        "glob": "allow",
        "grep": "deny",
        "external_directory": "deny",
        "edit": "deny",
    }
    if role in ["builder", "debugger"]:
        permission["edit"] = {
            "*": "deny",
            **{str(repo / p): "allow" for p in c["files"]},
            **dict.fromkeys(c["files"], "allow"),
        }
    config = {
        "model": model,
        "small_model": model,
        "share": "disabled",
        "permission": permission,
        "enabled_providers": ["opencode"],
        "agent": {
            "harness-worker": {
                "mode": "primary",
                "model": model,
                "steps": 24,
                "permission": permission,
                "prompt": (HOME / "CONSTITUTION.md").read_text()
                + "\n"
                + json.loads((HOME / "roles.json").read_text()).get(role, ""),
            }
        },
    }
    env = dict(
        os.environ, OPENCODE_CONFIG_CONTENT=json.dumps(config), OPENCODE_DISABLE_AUTOUPDATE="true"
    )
    return execute(
        [
            "opencode",
            "run",
            "--pure",
            "--agent",
            "harness-worker",
            "--model",
            model,
            "--format",
            "json",
            prompt,
        ],
        repo,
        timeout=routing.get("timeout_seconds", 240),
        env=env,
    )


def checks(c, repo, out, label):
    results = []
    for index, check in enumerate(c["checks"]):
        argv = list(check["argv"])
        xml = out / f"{label}-{index}.xml"
        if check["kind"] == "pytest":
            argv += ["--junitxml=" + str(xml)]
        try:
            r = execute(argv, repo, check.get("timeout_seconds", 180))
        except OSError as exc:
            r = dict(command=argv, exit_code=127, output=str(exc), timestamp=time.time())
        r["passed"] = r["exit_code"] == 0
        if check["kind"] == "pytest":
            try:
                root = ET.parse(xml).getroot()
                suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
                counts = {
                    k: sum(int(s.get(k, 0)) for s in suites)
                    for k in ["tests", "failures", "errors", "skipped"]
                }
                r["counts"] = counts
                r["passed"] &= (
                    counts["tests"] > 0
                    and counts["failures"] == 0
                    and counts["errors"] == 0
                    and counts["skipped"] == 0
                )
            except (OSError, ET.ParseError, ValueError):
                r["passed"] = False
                r["report_error"] = "Missing or invalid JUnit evidence"
        write(out / f"{label}-{index}.json", r)
        results.append(r)
    return results


def run(c, repo, out, routing, invoke=agent):
    validate(c)
    out.mkdir(parents=True, mode=0o700)
    write(out / "contract.json", c)
    state = dict(
        run_id=out.name,
        repository=str(repo),
        status="NOT_STARTED",
        attempts=[],
        routing=routing,
        contract=c,
        created=time.time(),
    )

    def save(status, reason=""):
        state.update(status=status, reason=reason, source=snapshot(repo))
        write(out / "status.json", state)
        with (out / "events.jsonl").open("a") as events:
            events.write(
                json.dumps({"timestamp": time.time(), "status": status, "reason": reason}) + "\n"
            )
        diff = execute(["git", "diff", "HEAD", "--"], repo)["output"]
        (out / "diff.patch").write_text(diff)
        packet = dict(
            task=c,
            status=status,
            reason=reason,
            source=state["source"],
            git_status=execute(["git", "status", "--short"], repo)["output"],
            attempts=state["attempts"],
            evidence=str(out),
            smallest_unresolved_question=reason or "Do all acceptance criteria hold?",
            architecture=c.get("context", []),
            hypotheses="See independent review and failure output; unknown unless evidenced.",
        )
        write(out / "handoff.json", packet)
        return state

    save("NOT_STARTED")
    for name in c["files"]:
        if not (repo / name).resolve().is_relative_to(repo) or (repo / name).is_symlink():
            return save("BLOCKED", "Edit path escapes repository or is a symlink: " + name)
    for dep in c["dependencies"]:
        d = json.loads(Path(dep).read_text())
        if (
            d["status"] != "VERIFIED"
            or d["repository"] != str(repo)
            or d["source"] != snapshot(repo)
        ):
            return save(
                "BLOCKED", "Prerequisite is not VERIFIED at current repository state: " + dep
            )
    if c.get("risk") in ["high", "security"]:
        return save(
            "BLOCKED", "Senior review required before implementation; no paid model invoked"
        )
    if routing["builder"] == routing["verifier"] or routing["debugger"] == routing["verifier"]:
        return save("BLOCKED", "Verifier must use a different model")
    baseline = snapshot(repo)
    save("IN_PROGRESS")
    initial = checks(c, repo, out, "baseline")
    state["baseline"] = initial
    context = json.dumps(c)
    failure = json.dumps({"baseline": initial})
    for attempt in range(1, 3):
        role = "builder" if attempt == 1 else "debugger"
        save("IN_PROGRESS")
        result = invoke(
            role,
            "Implement only the contract files. No shell, delegation, or external "
            "services. The harness runs checks. Report partial work honestly.\nCONTRACT\n"
            + context
            + "\nPRIOR FAILURE\n"
            + failure[-18000:],
            repo,
            c,
            routing,
        )
        write(out / f"{attempt}-{role}.json", result)
        item = dict(attempt=attempt, role=role, builder_exit=result["exit_code"])
        state["attempts"].append(item)
        current = snapshot(repo)
        item["builder_report"] = model_text(result)[-5000:]
        changed = [k for k in baseline.keys() | current.keys() if baseline.get(k) != current.get(k)]
        if any(k not in c["files"] for k in changed):
            return save("BLOCKED", "Out-of-contract changes: " + ", ".join(changed))
        if result["exit_code"]:
            return save("BLOCKED", "Builder process failed or timed out; inspect evidence")
        save("IMPLEMENTED_UNVERIFIED")
        save("VERIFYING")
        evidence = checks(c, repo, out, str(attempt))
        item["checks"] = evidence
        diff = execute(["git", "diff", "HEAD", "--"], repo)["output"]
        review = invoke(
            "verifier",
            "Independently read every contract file; inspect missing "
            "behavior and negative cases. Do not trust builder claims. The runner has "
            "independently executed these checks. Return ONLY JSON with verdict "
            "(VERIFIED, REJECTED, BLOCKED, PHYSICAL_VERIFICATION_REQUIRED), criteria "
            "(object mapping EVERY acceptance string to boolean), and findings (string). "
            "Never certify physical/human observations.\nCONTRACT\n"
            + context
            + "\nDIFF\n"
            + diff[-20000:]
            + "\nCHECKS\n"
            + json.dumps(evidence)[-20000:],
            repo,
            c,
            routing,
        )
        write(out / f"{attempt}-verifier.json", review)
        try:
            verdict = json.loads(
                model_text(review).strip().removeprefix("```json").removesuffix("```")
            )
        except (ValueError, TypeError):
            verdict = {"verdict": "BLOCKED", "findings": "Verifier returned no valid JSON"}
        if not isinstance(verdict, dict) or not isinstance(verdict.get("criteria", {}), dict):
            verdict = {"verdict": "BLOCKED", "findings": "Malformed review schema"}
        item["review"] = verdict
        if snapshot(repo) != current:
            return save("BLOCKED", "Source changed during verification; evidence is stale")
        passed = (
            all(e["passed"] for e in evidence)
            and review["exit_code"] == 0
            and verdict.get("verdict") == "VERIFIED"
            and all(verdict.get("criteria", {}).get(a) is True for a in c["acceptance"])
        )
        if passed:
            if c["gates"]:
                return save(
                    "PHYSICAL_VERIFICATION_REQUIRED",
                    "READY_FOR_PHYSICAL_VERIFICATION: " + ", ".join(c["gates"]),
                )
            return save("VERIFIED")
        failure = json.dumps(
            {"checks": evidence, "review": verdict, "prior_approach": item["builder_report"]}
        )
        save("FAILED", failure[-2000:])
        if verdict.get("verdict") in ["BLOCKED", "PHYSICAL_VERIFICATION_REQUIRED"]:
            return save(verdict["verdict"], str(verdict.get("findings")))
    return save(
        "BLOCKED", "Two verification failures. STOP; senior escalation packet: handoff.json"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["run", "status"])
    parser.add_argument("path", type=Path)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    args = parser.parse_args()
    repo = args.repo.resolve()
    if args.action == "status":
        state = json.loads((args.path / "status.json").read_text())
        if state["source"] != snapshot(Path(state["repository"])):
            state.update(
                status="IMPLEMENTED_UNVERIFIED", reason="Repository changed; evidence is stale"
            )
    else:
        routing = json.loads((HOME / "routing.json").read_text())
        out = (
            Path.home()
            / ".local/state/lifeos-harness"
            / (time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8])
        )
        try:
            state = run(json.loads(args.path.read_text()), repo, out, routing)
        except (ValueError, OSError, KeyError) as exc:
            if out.exists() and (out / "status.json").exists():
                state = json.loads((out / "status.json").read_text())
                state.update(status="BLOCKED", reason=str(exc))
                write(out / "status.json", state)
            raise SystemExit(str(exc)) from exc
        print("Evidence:", out)
    print(state["status"] + ": " + state.get("reason", ""))
    return 0 if state["status"] == "VERIFIED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
