---
name: clerk-branch
description: Start a new git feature or fix branch across affected repositories, refine scope, and create an implementation plan.
---

# Clerk Branch Workflow

When starting a new feature or fix task:

1. **Create Feature/Fix Branch**:
   - In all repositories affected by the task, create and switch to a new branch:
     ```bash
     git switch -c <feature|fix>/<short-name>
     ```
2. **Refine Scope**:
   - Review requirements and existing code/docs (`GEMINI.md`, `ROADMAP.md`, `README.rst`).
   - Ask clarifying questions if any requirements are ambiguous.
3. **Implementation Plan**:
   - Outline the specific files, modules, and tests to be created or modified before making changes.
