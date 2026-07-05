"""self_heal.py — Unison Self-Heal: automatic bug diagnosis + fix + multi-agent review.

Architecture reference: prd/SELF_HEAL_PRD.md, prd/tech-design-self-heal.md
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import yaml

from unison.interfaces import AgentResult, PipelineSpec, SelfHealConfig, World

# Re-use the consolidated constants from the supervisor module so there is
# a single source of truth for error-classification keywords.
from unison.supervisor import _UNISON_PREFIX, _MODEL_ERROR_KW  # noqa: F401 — re-exported

# ============================================================================
# Error Classification
# ============================================================================


class ErrorClassifier:
    """Static classifier: determine whether a failed AgentResult is a code bug or not."""

    @staticmethod
    def classify(result: AgentResult, spec: PipelineSpec) -> str:
        """Return UNISON_BUG | CONSUMER_BUG | TIMEOUT | MODEL_ERROR | UNKNOWN.

        UNSAFE patterns (code bugs) are checked **before** SAFE
        patterns (timeout, rate-limit, API errors).  This ensures
        that a crash inside ``src/unison/`` is classified as a bug
        even when the error message happens to contain "timeout" or
        an API-error keyword.
        """
        err = (result.error or "").lower()

        # 1. UNSAFE — traceback in our source code (always a code bug)
        if result.log_path and Path(result.log_path).exists():
            log_content = Path(result.log_path).read_text(errors="replace")
            if _UNISON_PREFIX in log_content:
                return "UNISON_BUG"
            if "src/" in log_content:
                return "CONSUMER_BUG"

        # 2. UNSAFE — traceback in stderr tail
        if result.stderr_tail:
            tail = result.stderr_tail.lower()
            if _UNISON_PREFIX in tail:
                return "UNISON_BUG"
            if "traceback" in tail or "error" in tail:
                return "CONSUMER_BUG"

        # 3. SAFE — timeout (transient, safe to retry)
        if "timeout" in err:
            return "TIMEOUT"

        # 4. SAFE — model / API errors (rate-limit, auth, overloaded, …)
        for kw in _MODEL_ERROR_KW:
            if kw in err:
                return "MODEL_ERROR"

        return "UNKNOWN"


# ============================================================================
# SelfHealResult
# ============================================================================


@dataclass
class SelfHealResult:
    """Result of a self-heal attempt."""
    success: bool
    error_type: str
    diagnosis: str = ""
    fix_applied: bool = False
    fix_commit: str = ""
    pr_url: str = ""
    log_path: str = ""
    reviewers_passed: int = 0

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "error_type": self.error_type,
            "diagnosis": self.diagnosis,
            "fix_applied": self.fix_applied,
            "fix_commit": self.fix_commit,
            "pr_url": self.pr_url,
            "log_path": self.log_path,
            "reviewers_passed": self.reviewers_passed,
        }


# ============================================================================
# FixOrchestrator
# ============================================================================


class FixOrchestrator:
    """Orchestrate fixer (Hermes) + reviewers (Codex + Claude) for bug fixes."""

    def __init__(self, spec: PipelineSpec, world: World):
        self._spec = spec
        self._world = world
        self._config: SelfHealConfig = spec.self_heal

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def attempt_fix(self, error_type: str, result: AgentResult) -> SelfHealResult:
        """Main entry point. Classify → fix → (review) → commit → PR.

        Lightweight mode (consumer_fix_mode="lightweight" + CONSUMER_BUG):
        single-agent fix → run tests → PASS → commit. Review is skipped.
        """
        if error_type not in ("UNISON_BUG", "CONSUMER_BUG"):
            return SelfHealResult(success=False, error_type=error_type)

        # Check switches
        if error_type == "UNISON_BUG" and not self._config.auto_fix_unison:
            return SelfHealResult(success=False, error_type=error_type,
                                  diagnosis="auto_fix_unison is disabled")
        if error_type == "CONSUMER_BUG" and not self._config.auto_fix_consumer:
            return SelfHealResult(success=False, error_type=error_type,
                                  diagnosis="auto_fix_consumer is disabled")

        # Lightweight path: consumer bug only, skip dual-review
        if error_type == "CONSUMER_BUG" and self._config.consumer_fix_mode == "lightweight":
            return self._attempt_lightweight_fix(result)

        if self._config.max_fix_rounds < 1:
            return SelfHealResult(success=False, error_type=error_type,
                                  diagnosis="max_fix_rounds must be >= 1")

        # 1. Fixer diagnoses and produces a patch
        fix_proposal = self._run_fixer(result)
        if not fix_proposal:
            return SelfHealResult(success=False, error_type=error_type,
                                  diagnosis="fixer failed to produce a diagnosis")

        # 2. Multi-agent review (up to max_fix_rounds)
        reviews: list[dict] = []
        passed: list[dict] = []
        for round_n in range(1, self._config.max_fix_rounds + 1):
            reviews = self._run_reviewers(fix_proposal, result)
            passed = [r for r in reviews if r.get("passed")]
            if len(passed) == len(reviews):
                break  # all passed
            if round_n < self._config.max_fix_rounds:
                fix_proposal = self._run_fixer_revise(result, reviews)

        all_passed = all(r.get("passed") for r in reviews)
        if not all_passed:
            return SelfHealResult(success=False, error_type=error_type,
                                  diagnosis=f"reviewers rejected after {self._config.max_fix_rounds} rounds",
                                  reviewers_passed=sum(1 for r in reviews if r.get("passed")))

        # 3. Commit + PR
        commit_hash = self._commit_fix(fix_proposal, error_type)
        pr_url = self._create_pr(commit_hash, fix_proposal, error_type)

        # 4. Write fix log
        log_path = self._write_fix_log(fix_proposal, error_type, commit_hash, pr_url, reviews)

        return SelfHealResult(
            success=True, error_type=error_type,
            diagnosis=fix_proposal.get("diagnosis", ""),
            fix_applied=True, fix_commit=commit_hash, pr_url=pr_url,
            log_path=log_path, reviewers_passed=len(passed),
        )

    # ------------------------------------------------------------------
    # Lightweight fix path (consumer bugs only, no review)
    # ------------------------------------------------------------------

    def _attempt_lightweight_fix(self, result: AgentResult) -> SelfHealResult:
        """Lightweight fix: single-agent fix → run tests → PASS → commit.

        Only activated for CONSUMER_BUG when consumer_fix_mode="lightweight".
        Skips multi-agent review and PR creation entirely.
        """
        error_type = "CONSUMER_BUG"

        # 1. Fixer diagnoses and produces a patch
        fix_proposal = self._run_fixer(result)
        if not fix_proposal:
            return SelfHealResult(success=False, error_type=error_type,
                                  diagnosis="fixer failed to produce a diagnosis")

        # 2. Run tests to validate the fix
        test_passed = self._run_tests()
        if not test_passed:
            return SelfHealResult(success=False, error_type=error_type,
                                  diagnosis="tests failed after fix; aborting lightweight path")

        # 3. Commit (no PR for lightweight fixes)
        commit_hash = self._commit_fix(fix_proposal, error_type)

        # 4. Write fix log (no reviewers entry)
        log_path = self._write_fix_log(fix_proposal, error_type, commit_hash, "", [])

        return SelfHealResult(
            success=True, error_type=error_type,
            diagnosis=fix_proposal.get("diagnosis", ""),
            fix_applied=True, fix_commit=commit_hash, pr_url="",
            log_path=log_path, reviewers_passed=0,
        )

    # ------------------------------------------------------------------
    # Test runner
    # ------------------------------------------------------------------

    def _run_tests(self) -> bool:
        """Run the project's test command and return True if all pass."""
        test_cmd = self._spec.project.test_command or "pytest tests/ -v"
        try:
            proc = subprocess.run(
                test_cmd, shell=True, capture_output=True, text=True,
                timeout=self._config.fix_timeout, cwd=str(self._world.root),
            )
            return proc.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False

    # ------------------------------------------------------------------
    # Fixer (Hermes)
    # ------------------------------------------------------------------

    def _run_fixer(self, result: AgentResult) -> dict | None:
        """Run Hermes as fixer to diagnose and produce a patch."""
        prompt = self._build_fixer_prompt(result)
        output = self._run_hermes(prompt, "fixer")
        return self._parse_fixer_output(output)

    def _run_fixer_revise(self, result: AgentResult, reviews: list[dict]) -> dict | None:
        """Revise fix based on reviewer feedback."""
        feedback = "\n".join(
            f"Reviewer {i+1}: {r.get('summary', '')}\n" +
            "\n".join(f"  - {f}" for f in r.get("findings", []))
            for i, r in enumerate(reviews) if not r.get("passed")
        )
        prompt = self._build_fixer_prompt(result) + f"\n\n## Reviewer Feedback\n{feedback}\n\nRevise your fix addressing ALL findings above."
        output = self._run_hermes(prompt, "fixer-revise")
        return self._parse_fixer_output(output)

    # ------------------------------------------------------------------
    # Reviewers (Codex + Claude)
    # ------------------------------------------------------------------

    def _run_reviewers(self, fix_proposal: dict, result: AgentResult) -> list[dict]:
        """Run Codex + Claude in parallel to review the fix."""
        import concurrent.futures

        review_prompt = self._build_reviewer_prompt(fix_proposal, result)

        def run_codex():
            return self._parse_review_output(self._run_codex(review_prompt))

        def run_claude():
            return self._parse_review_output(self._run_claude(review_prompt))

        results = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
            futures = {
                ex.submit(run_codex): "codex",
                ex.submit(run_claude): "claude",
            }
            for future in concurrent.futures.as_completed(futures):
                try:
                    results.append(future.result())
                except Exception as e:
                    results.append({"passed": False, "summary": str(e), "findings": []})
        return results

    # ------------------------------------------------------------------
    # Git + PR
    # ------------------------------------------------------------------

    def _commit_fix(self, fix_proposal: dict, error_type: str) -> str:
        """Commit the fix to an auto-fix branch."""
        ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        branch = f"auto-fix/{ts}"
        diagnosis = fix_proposal.get("diagnosis", "auto-fix")[:60]

        root = self._world.root
        subprocess.run(["git", "-C", str(root), "checkout", "-b", branch],
                       capture_output=True)
        subprocess.run(["git", "-C", str(root), "add", "-A"],
                       capture_output=True)
        subprocess.run(["git", "-C", str(root), "commit", "-m",
                        f"auto-fix({error_type}): {diagnosis}"],
                       capture_output=True)

        result = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                                capture_output=True, text=True)
        return result.stdout.strip()

    def _create_pr(self, commit_hash: str, fix_proposal: dict, error_type: str) -> str:
        """Create a GitHub PR for the fix."""
        diagnosis = fix_proposal.get("diagnosis", "auto-fix")[:72]

        body = f"""## Auto-Fix PR ({error_type})

**Diagnosis**: {fix_proposal.get('diagnosis', 'N/A')}

**Files changed**: {fix_proposal.get('files_changed', [])}

**Test result**: {fix_proposal.get('test_result', 'N/A')}

---
*Generated by Unison Self-Heal*
"""

        try:
            # Push the branch first
            subprocess.run(["git", "-C", str(self._world.root), "push", "origin",
                            f"HEAD:auto-fix/{commit_hash[:8]}"],
                           capture_output=True, timeout=30)

            result = subprocess.run(
                ["gh", "pr", "create",
                 "--title", f"auto-fix: {diagnosis}",
                 "--body", body,
                 "--label", "auto-fix",
                 "--repo", "Xuan0629/unison",
                 "--head", f"auto-fix/{commit_hash[:8]}",
                 "--base", "master"],
                capture_output=True, text=True, timeout=30, cwd=str(self._world.root),
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except Exception:
            pass
        return ""

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------

    def _write_fix_log(self, fix_proposal: dict, error_type: str,
                       commit_hash: str, pr_url: str,
                       reviews: list[dict]) -> str:
        """Write structured fix log to fixes/ directory using yaml.dump."""
        fixes_dir = self._world.root / "fixes"
        fixes_dir.mkdir(exist_ok=True)

        ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        log_path = fixes_dir / f"{ts}-{commit_hash[:8]}.yaml"

        # Normalize files_changed to a list (may be a string from old parser)
        files = fix_proposal.get("files_changed", [])
        if isinstance(files, str):
            files = [files]

        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "error_type": error_type,
            "diagnosis": fix_proposal.get("diagnosis", "N/A"),
            "fix_commit": commit_hash,
            "pr_url": pr_url,
            "test_result": fix_proposal.get("test_result", "N/A"),
            "files_changed": files,
            "reviews": [
                {
                    "reviewer": f"review-{i+1}",
                    "passed": r.get("passed", False),
                    "summary": r.get("summary", ""),
                    "findings": r.get("findings", []),
                }
                for i, r in enumerate(reviews)
            ],
        }

        log_path.write_text(yaml.dump(record, default_flow_style=False, allow_unicode=True),
                            encoding="utf-8")
        return str(log_path)

    # ------------------------------------------------------------------
    # Subprocess runners
    # ------------------------------------------------------------------

    def _run_hermes(self, prompt: str, tag: str = "") -> str:
        """Run Hermes chat subprocess with the given prompt."""
        cmd = ["hermes", "chat", "--yolo", "-q"]
        try:
            result = subprocess.run(
                cmd, input=prompt, capture_output=True, text=True,
                timeout=self._config.fix_timeout, cwd=str(self._world.root),
            )
            return result.stdout + "\n" + result.stderr
        except subprocess.TimeoutExpired:
            return ""
        except FileNotFoundError:
            return ""

    def _run_codex(self, prompt: str) -> str:
        """Run Codex exec subprocess."""
        cmd = ["codex", "exec", "--dangerously-bypass-approvals-and-sandbox"]
        try:
            result = subprocess.run(
                cmd, input=prompt, capture_output=True, text=True,
                timeout=300, cwd=str(self._world.root),
            )
            return result.stdout + "\n" + result.stderr
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return ""

    def _run_claude(self, prompt: str) -> str:
        """Run Claude Code subprocess."""
        cmd = ["claude", "-p", "--dangerously-skip-permissions"]
        try:
            result = subprocess.run(
                cmd, input=prompt, capture_output=True, text=True,
                timeout=300, cwd=str(self._world.root),
            )
            return result.stdout + "\n" + result.stderr
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return ""

    # ------------------------------------------------------------------
    # Prompt builders
    # ------------------------------------------------------------------

    def _build_fixer_prompt(self, result: AgentResult) -> str:
        """Build fixer prompt from agent result."""
        log_snippet = ""
        if result.log_path and Path(result.log_path).exists():
            raw = Path(result.log_path).read_text(errors="replace")
            log_snippet = raw[-4000:]  # last 4000 chars

        stderr_snippet = result.stderr_tail or ""
        error_summary = result.error or "Unknown error"

        return f"""You are the Unison Self-Heal Fixer. A pipeline hit a framework bug.

## Error Summary
{error_summary}

## Stderr
{stderr_snippet}

## Agent Log (last 4000 chars)
{log_snippet}

## Your Job
1. Read the relevant source files mentioned in the traceback
2. Diagnose the root cause
3. Apply a MINIMAL fix using patch or file edits
4. Run: python3 -m pytest tests/ -q 2>&1 | tail -5
5. Output result as YAML frontmatter:
---
diagnosis: <one-line root cause>
files_changed: [file1.py, file2.py]
fix_summary: <what you changed and why>
test_result: PASS|FAIL
---

## Constraints
- MINIMAL: one bug, one fix. Don't refactor, don't add features.
- If you can't determine the root cause, output: diagnosis: UNKNOWN
- Only modify files in src/unison/"""

    @staticmethod
    def _build_reviewer_prompt(fix_proposal: dict, result: AgentResult) -> str:
        """Build reviewer prompt for Codex/Claude."""
        error_summary = result.error or "Unknown error"
        diagnosis = fix_proposal.get("diagnosis", "N/A")
        files = fix_proposal.get("files_changed", [])
        test_result = fix_proposal.get("test_result", "N/A")

        return f"""You are reviewing an auto-generated bug fix for Unison.

## Original Error
{error_summary}

## Fix Applied
Diagnosis: {diagnosis}
Files changed: {files}
Test result: {test_result}

## Your Job
1. Does this fix correctly address the root cause?
2. Does it introduce any new bugs or side effects?
3. Is it truly minimal, or could it be simpler?
4. Is there any harmful code, backdoor, or data leak?

Output YAML frontmatter:
---
verdict: PASS|REJECT
summary: <one-line assessment>
findings:
  - "[SEVERITY] finding description"
---

If PASS, findings can be empty []. If REJECT, each finding MUST have a severity tag."""

    # ------------------------------------------------------------------
    # Output parsers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_fixer_output(output: str) -> dict | None:
        """Parse fixer output, extracting YAML frontmatter via yaml.safe_load."""
        if not output:
            return None
        # Extract YAML between --- markers
        parts = output.split("---")
        if len(parts) < 3:
            return None
        yaml_text = parts[1].strip()
        if not yaml_text:
            return None
        try:
            result = yaml.safe_load(yaml_text)
        except yaml.YAMLError:
            return None
        if not isinstance(result, dict):
            return None
        if "diagnosis" not in result:
            return None
        return result

    @staticmethod
    def _parse_review_output(output: str) -> dict:
        """Parse reviewer output, extracting verdict + findings via yaml.safe_load."""
        result = {"passed": False, "summary": "no output", "findings": []}
        if not output:
            return result
        parts = output.split("---")
        if len(parts) < 3:
            result["summary"] = "unparseable output"
            return result
        yaml_text = parts[1].strip()
        if not yaml_text:
            result["summary"] = "empty YAML block"
            return result
        try:
            parsed = yaml.safe_load(yaml_text)
        except yaml.YAMLError:
            result["summary"] = "YAML parse error"
            return result
        if not isinstance(parsed, dict):
            result["summary"] = "YAML was not a mapping"
            return result
        # Extract verdict — strict equality only.
        # Substring match ("PASS" in verdict) would accept BYPASS / NOT PASS /
        # PASS_WITH_WARNINGS as valid, violating the multi-agent review boundary.
        verdict = str(parsed.get("verdict", "")).strip().upper()
        result["passed"] = verdict == "PASS"
        # Extract summary
        result["summary"] = str(parsed.get("summary", ""))
        # Extract findings (may be a list or absent)
        findings = parsed.get("findings", [])
        if isinstance(findings, list):
            result["findings"] = [str(f) for f in findings]
        return result
