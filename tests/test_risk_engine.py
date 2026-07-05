"""Tests for risk_engine.py — RuleEngineRiskEvaluator (3-tuple rules)."""
import tempfile
from pathlib import Path
import pytest

from unison.risk_engine import RuleEngineRiskEvaluator, RiskEvaluation
from unison.interfaces import RiskLevel, Operation, RiskMatrixConfig


class TestRuleEngineRiskEvaluator:
    """RuleEngineRiskEvaluator tests."""

    def test_create_evaluator(self, tmp_path):
        """Create a RuleEngineRiskEvaluator."""
        matrix = RiskMatrixConfig()
        evaluator = RuleEngineRiskEvaluator(matrix=matrix, workspace=tmp_path)
        assert evaluator.matrix == matrix
        assert evaluator.workspace == tmp_path

    def test_evaluate_sudo_command_l3(self, tmp_path):
        """sudo command is unconditional L3."""
        matrix = RiskMatrixConfig()
        evaluator = RuleEngineRiskEvaluator(matrix=matrix, workspace=tmp_path)
        
        result = evaluator.evaluate(
            operation=Operation.MODIFY,
            path="/etc/passwd",
            command="sudo rm -rf /"
        )
        
        assert result.level == RiskLevel.L3
        assert "sudo" in result.reason.lower()

    def test_evaluate_system_critical_path_l3(self, tmp_path):
        """System critical paths are L3."""
        matrix = RiskMatrixConfig(
            system_critical_paths=[
                "/etc/passwd",
                "/etc/shadow",
                "~/.ssh/id_*",
            ]
        )
        evaluator = RuleEngineRiskEvaluator(matrix=matrix, workspace=tmp_path)
        
        result = evaluator.evaluate(
            operation=Operation.READ,
            path="/etc/passwd"
        )
        
        assert result.level == RiskLevel.L3

    def test_evaluate_ssh_key_l3(self, tmp_path):
        """SSH private keys are L3."""
        matrix = RiskMatrixConfig(
            system_critical_paths=["~/.ssh/id_*"]
        )
        evaluator = RuleEngineRiskEvaluator(matrix=matrix, workspace=tmp_path)
        
        result = evaluator.evaluate(
            operation=Operation.READ,
            path=str(Path.home() / ".ssh" / "id_rsa")
        )
        
        assert result.level == RiskLevel.L3

    def test_evaluate_workspace_read_l1(self, tmp_path):
        """Workspace read is L1 (auto-allow session)."""
        matrix = RiskMatrixConfig()
        evaluator = RuleEngineRiskEvaluator(matrix=matrix, workspace=tmp_path)
        
        # Create a file in workspace
        test_file = tmp_path / "test.py"
        test_file.write_text("print('hello')")
        
        result = evaluator.evaluate(
            operation=Operation.READ,
            path=str(test_file)
        )
        
        assert result.level == RiskLevel.L1

    def test_evaluate_workspace_create_l1(self, tmp_path):
        """Workspace create is L1 (auto-allow session)."""
        matrix = RiskMatrixConfig()
        evaluator = RuleEngineRiskEvaluator(matrix=matrix, workspace=tmp_path)
        
        result = evaluator.evaluate(
            operation=Operation.CREATE,
            path=str(tmp_path / "new_file.py")
        )
        
        assert result.level == RiskLevel.L1

    def test_evaluate_workspace_modify_l2(self, tmp_path):
        """Workspace modify is L2 (observer evaluate)."""
        matrix = RiskMatrixConfig()
        evaluator = RuleEngineRiskEvaluator(matrix=matrix, workspace=tmp_path)
        
        # Create a file in workspace
        test_file = tmp_path / "existing.py"
        test_file.write_text("print('hello')")
        
        result = evaluator.evaluate(
            operation=Operation.MODIFY,
            path=str(test_file)
        )
        
        assert result.level == RiskLevel.L2

    def test_evaluate_workspace_delete_l2(self, tmp_path):
        """Workspace delete is L2 (observer evaluate)."""
        matrix = RiskMatrixConfig()
        evaluator = RuleEngineRiskEvaluator(matrix=matrix, workspace=tmp_path)
        
        result = evaluator.evaluate(
            operation=Operation.DELETE,
            path=str(tmp_path / "file.py")
        )
        
        assert result.level == RiskLevel.L2

    def test_evaluate_external_read_l0(self, tmp_path):
        """External read is L0 (auto-allow)."""
        matrix = RiskMatrixConfig()
        evaluator = RuleEngineRiskEvaluator(matrix=matrix, workspace=tmp_path)
        
        result = evaluator.evaluate(
            operation=Operation.READ,
            path="/tmp/external_file.txt"
        )
        
        assert result.level == RiskLevel.L0

    def test_evaluate_external_create_l2(self, tmp_path):
        """External create is L2 (observer evaluate)."""
        matrix = RiskMatrixConfig()
        evaluator = RuleEngineRiskEvaluator(matrix=matrix, workspace=tmp_path)
        
        result = evaluator.evaluate(
            operation=Operation.CREATE,
            path="/tmp/new_external.txt"
        )
        
        assert result.level == RiskLevel.L2

    def test_evaluate_known_safe_command_downgrade(self, tmp_path):
        """Known safe external commands downgrade risk by one level."""
        matrix = RiskMatrixConfig(
            known_safe_external_commands=[
                "pip install *",
                "npm install *",
            ]
        )
        evaluator = RuleEngineRiskEvaluator(matrix=matrix, workspace=tmp_path)
        
        # External create would normally be L2, but pip install downgrades to L1
        result = evaluator.evaluate(
            operation=Operation.CREATE,
            path="/usr/lib/python3/site-packages/package",
            command="pip install requests"
        )
        
        assert result.level == RiskLevel.L1

    def test_evaluate_unknown_path_l2(self, tmp_path):
        """Unknown path defaults to L2."""
        matrix = RiskMatrixConfig()
        evaluator = RuleEngineRiskEvaluator(matrix=matrix, workspace=tmp_path)
        
        result = evaluator.evaluate(
            operation=Operation.MODIFY,
            path="/some/unknown/path"
        )
        
        assert result.level == RiskLevel.L2

    def test_is_known_safe_command_true(self, tmp_path):
        """is_known_safe_command returns True for matching command."""
        matrix = RiskMatrixConfig(
            known_safe_external_commands=["pip install *", "npm install *"]
        )
        evaluator = RuleEngineRiskEvaluator(matrix=matrix, workspace=tmp_path)
        
        assert evaluator.is_known_safe_command("pip install requests") is True
        assert evaluator.is_known_safe_command("npm install lodash") is True

    def test_is_known_safe_command_false(self, tmp_path):
        """is_known_safe_command returns False for non-matching command."""
        matrix = RiskMatrixConfig(
            known_safe_external_commands=["pip install *"]
        )
        evaluator = RuleEngineRiskEvaluator(matrix=matrix, workspace=tmp_path)
        
        assert evaluator.is_known_safe_command("rm -rf /") is False
        assert evaluator.is_known_safe_command("sudo apt install") is False

    def test_is_system_critical_path_true(self, tmp_path):
        """is_system_critical_path returns True for critical paths."""
        matrix = RiskMatrixConfig(
            system_critical_paths=["/etc/passwd", "~/.ssh/id_*"]
        )
        evaluator = RuleEngineRiskEvaluator(matrix=matrix, workspace=tmp_path)
        
        assert evaluator.is_system_critical_path("/etc/passwd") is True
        assert evaluator.is_system_critical_path(str(Path.home() / ".ssh" / "id_rsa")) is True

    def test_is_system_critical_path_false(self, tmp_path):
        """is_system_critical_path returns False for non-critical paths."""
        matrix = RiskMatrixConfig(
            system_critical_paths=["/etc/passwd"]
        )
        evaluator = RuleEngineRiskEvaluator(matrix=matrix, workspace=tmp_path)
        
        assert evaluator.is_system_critical_path("/tmp/safe.txt") is False


class TestRiskEvaluation:
    """RiskEvaluation dataclass tests."""

    def test_create_evaluation(self):
        """Create a RiskEvaluation."""
        eval_result = RiskEvaluation(
            level=RiskLevel.L2,
            reason="External file modification",
            snapshot_path=Path("/tmp/snapshot"),
            halted=False
        )
        
        assert eval_result.level == RiskLevel.L2
        assert eval_result.reason == "External file modification"
        assert eval_result.snapshot_path == Path("/tmp/snapshot")
        assert eval_result.halted is False

    def test_create_evaluation_l3_halted(self):
        """Create a RiskEvaluation with L3 halt."""
        eval_result = RiskEvaluation(
            level=RiskLevel.L3,
            reason="sudo command detected",
            halted=True
        )
        
        assert eval_result.level == RiskLevel.L3
        assert eval_result.halted is True
        assert eval_result.snapshot_path is None
