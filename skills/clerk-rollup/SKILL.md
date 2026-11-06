---
name: clerk-rollup
description: Rollup changes in the current feature branch, update documentation and version, merge to main, tag, and push.
---

# Clerk Rollup Workflow

To rollup and release changes from the current feature branch:

1. **Inspect Branch Changes**:
   - Run `git status` and `git diff main` to review all changes since branching from `main`.
2. **Update Documentation**:
   - Update `CHANGELOG.md` / `CHANGELOG.rst`, `README.rst`, and `GEMINI.md` to reflect the changes.
3. **Bump Version**:
   - Increment the version number (`__version__`) in `src/<namespace>/<repo_name>/__init__.py`.
4. **Commit, Merge, and Tag**:
   - Stage and commit all changes with a clear subject and bulleted body.
   - Switch to `main` and merge the feature branch (`git switch main` and `git merge <branch-name>`).
   - Push `main` and the new version tag (`git tag vX.Y.Z` and `git push --tags`).
   - Delete the merged feature branch (`git branch -d <branch-name>`).
