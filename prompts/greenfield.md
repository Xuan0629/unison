---
name: greenfield
description: Greenfield mode template — build a NEW module from scratch without reading existing code. Used by Unison's greenfield pipeline mode.
---

# Greenfield Mode — 绿场模式

## CRITICAL RULES

This is a **GREENFIELD** task. You are building a NEW module from scratch.

1. **Do NOT read existing source files.** Your workspace is limited to the files listed below.
2. **Do NOT modify existing files.** Only create new files.
3. **Do NOT reference existing patterns.** This is a new module — define your own patterns.
4. **Focus on the TODO markers** in the skeleton file(s) provided.
5. **Match the skeleton's style** (type hints, dataclasses, docstrings).

## Workspace

You may ONLY read and write these files:

{FILE_LIST}

## Task

{TASK_DESCRIPTION}

## Verification

When done, verify with: `{TEST_COMMAND}`

If you encounter missing imports or dependencies:
1. Check if they exist in the project by searching ONLY for import statements — do not read implementation
2. If they don't exist, define minimal interfaces yourself
3. Never read existing source file bodies
