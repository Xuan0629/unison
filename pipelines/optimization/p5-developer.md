# Developer — P5: Implement MoA Mode

Read PRD + tech-design. Implement in order:

1. interfaces.py: MoaConfig + "moa" in PipelineMode
2. phase_router.py: moa PhaseDef sequence
3. pipeline.py: _build_moa() + parse moa YAML section
4. orchestrator.py: _run_moa_analyze(), _run_moa_synthesis(), routing
5. prompt_registry.py: moa-analyzer, moa-synthesizer task templates
6. tests/test_moa.py: MoaConfig, phase sequence, round file discovery

Reuse existing patterns: _invoke_agents_parallel for analyze phase. Synthesizer: single agent invocation writing synthesis file.

Verify: pytest tests/ -v -x --timeout=30
