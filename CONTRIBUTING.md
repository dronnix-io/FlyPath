# Contributing to FlyPath

Thanks for your interest in improving FlyPath. This guide covers how to propose
changes. It applies to everyone outside the core maintainer, and keeps the
history clean and the review simple.

## How contributions work

FlyPath uses a **fork and pull request** workflow. You do not need write access
to the repository. You work on your own fork and open a pull request; the
maintainer reviews it and merges it.

Merging to `main`, tagging versions, and publishing releases are done by the
maintainer (Salar Ghaffarian) only.

## Before you start

- Enable **two factor authentication** on your GitHub account.
- Your commits are credited to whatever email your git is configured with, so
  set it to an address you are happy to have shown in the public history:
  `git config user.email you@example.com`. If you are part of Dronnix and want
  your commits to represent Dronnix, use your `@dronnix.com` address; otherwise
  your own email is fine.

## Step by step

1. **Fork** the repository on GitHub (the Fork button on
   `https://github.com/dronnix-io/FlyPath`).

2. **Clone your fork** and add the main repository as `upstream`:

   ```bash
   git clone https://github.com/<your-username>/FlyPath.git
   cd FlyPath
   git remote add upstream https://github.com/dronnix-io/FlyPath.git
   ```

3. **Create a branch** off an up to date `main`:

   ```bash
   git fetch upstream
   git checkout -b my-change upstream/main
   ```

   Use a short, descriptive branch name (for example `fix-auto-direction`,
   `takeoff-zone-tolerance`).

4. **Make your change.** Keep each pull request focused on one thing. Match the
   style of the surrounding code.

5. **Run the checks locally** before pushing:

   ```bash
   python -m pyflakes $(git ls-files '*.py')
   python tools/check_qt6_enums.py
   python -m pytest -q tests
   ```

   The same checks run automatically on every pull request through CI, so a
   green run here means a green run there.

6. **Push to your fork** and open a pull request against `dronnix-io/FlyPath`
   `main`:

   ```bash
   git push origin my-change
   ```

   GitHub will offer a "Compare and pull request" button. Fill in the pull
   request template so the reviewer knows what changed and how you tested it.

## Keeping your branch current

If `main` moves ahead while your pull request is open:

```bash
git fetch upstream
git rebase upstream/main
git push origin my-change --force-with-lease
```

## Guidelines

- **Qt 5 and Qt 6 compatibility.** FlyPath runs on QGIS 3 (PyQt5) and QGIS 4
  (PyQt6). Use `qgis.PyQt` imports and scoped enums with a `getattr` fallback so
  both work. The Qt6 enum check enforces this.
- **Tests.** Logic that can be tested without QGIS (routing, splitting,
  contours) has pure Python tests under `tests/`. Add or update tests when you
  change that logic.
- **Commit messages.** Write a short summary line in the imperative mood, then a
  blank line and detail if needed.
- **Scope.** Large or structural changes are easier to land if you open an issue
  first to agree on the approach.

## Reporting bugs and requesting features

Open an issue at
`https://github.com/dronnix-io/FlyPath/issues`. For a bug, include your QGIS
version, your drone model, the steps to reproduce, and the mission KMZ if you
can share it.

## Questions and contact

For anything about a specific change, open an issue or comment on your pull
request so the discussion stays with the code. For anything else, reach the
maintainer at [salar@dronnix.com](mailto:salar@dronnix.com).
