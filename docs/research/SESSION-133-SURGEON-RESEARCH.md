# P0-SESSION-130-IDEMPOTENT-SURGEON — Upstream Research Digest

> This document is the **binding authority** for the code landed in SESSION-133.
> It condenses three top-tier industry/academic references into concrete code-level
> obligations for `mathart/workspace/asset_injector.py`,
> `mathart/workspace/atomic_downloader.py`, and
> `mathart/workspace/idempotent_surgeon.py`.

---

## 1. Ansible / Terraform — Idempotency as the Core Execution Philosophy

**Source anchors**
- Ansible "Validating tasks: check mode and diff mode"
  (<https://docs.ansible.com/projects/ansible/latest/playbook_guide/playbooks_checkmode.html>)
- HashiCorp "Detecting and Managing Drift with Terraform"
  (<https://www.hashicorp.com/en/blog/detecting-and-managing-drift-with-terraform>)

**Distilled principles**

| Principle | Origin | Our binding |
|---|---|---|
| **Desired-state reconciliation** | Ansible modules compare the declared state against the observed state before emitting a `changed` flag. | Every `Action` produced by `idempotent_surgeon` MUST first probe observed state via `PreflightReport`; if already satisfied, return `ActionOutcome.SKIPPED_ALREADY_SATISFIED` in microseconds. |
| **check_mode = pure simulation** | `ansible-playbook --check` performs zero side effects. | The Surgeon exposes a `dry_run=True` flag that reuses the full planning pipeline but short-circuits before any filesystem or network mutation. |
| **Drift detection precedes apply** | Terraform's `plan` refreshes state before `apply`. | Surgeon re-invokes `PreflightRadar.scan()` _after_ every write to confirm convergence, and records the post-apply verdict in the assembly report. |
| **Idempotency on re-run** | Running an Ansible play twice must be a no-op on the second run. | Mandatory end-to-end test: the second `surgeon.operate(report)` call MUST complete under 50 ms with **zero** `NETWORK` or `MUTATE` actions emitted. |

---

## 2. HuggingFace Cache + pnpm CAS — Zero-Copy Disk Reuse for Giant Artifacts

**Source anchors**
- HuggingFace "Understand caching"
  (<https://huggingface.co/docs/huggingface_hub/en/guides/manage-cache>)
- pnpm "Frequently Asked Questions"
  (<https://pnpm.io/faq>)

**Distilled principles**

| Principle | Origin | Our binding |
|---|---|---|
| **Content-addressable store (CAS)** | HF stores unique blobs under `blobs/<sha256>` and points revisions there via symlinks; pnpm uses a global `.pnpm-store` with hard links. | `AssetInjector` MUST first scan well-known AI caches (`~/.cache/huggingface/hub`, `%LOCALAPPDATA%/huggingface/hub`, `~/.cache/torch`, `~/ComfyUI/models`, user-declared `extra_search_roots`) for a matching blob **before** any network fetch. |
| **Symlink preferred** | HF snapshots are symlinks into `blobs/`; gives revision sharing with zero duplication. | First-choice injection strategy: `os.symlink(source_blob, target_path)` after `Path.resolve()`-ing the source. |
| **Hard-link fallback** | pnpm prefers hard links because Node's symlink semantics vary across OSes. | If `os.symlink` raises (Windows `WinError 1314` / cross-device / unsupported FS), fall back to `os.link()` (hard link). |
| **Cross-device copy fallback** | pnpm docs explicitly state "if the store is on a different drive, packages will be copied, not linked." | If even hard-link fails (cross-filesystem on POSIX, ERROR_NOT_SAME_DEVICE on Windows), fall back to `shutil.copy2()`. |
| **No blind overwrite** | HF cache never deletes user blobs; it only adds new hash dirs. | If the target path exists with a mismatching hash, rename it to `<path>.bak-<timestamp>` rather than `os.remove()` — preserves the user's "undo pill". |

---

## 3. aria2 / POSIX rename — Atomic Downloads & Resumable Transfers

**Source anchors**
- aria2 control-file discussion (<https://github.com/aria2/aria2/issues/792>)
- MDN "HTTP Range requests"
  (<https://developer.mozilla.org/en-US/docs/Web/HTTP/Guides/Range_requests>)
- POSIX `rename()` spec
  (<https://pubs.opengroup.org/onlinepubs/9799919799/functions/rename.html>)

**Distilled principles**

| Principle | Origin | Our binding |
|---|---|---|
| **Separate in-flight file from final file** | aria2 writes to `<name>.aria2` control file + `<name>` data file while the transfer is alive. | `AtomicDownloader` writes all bytes into `<target>.part`. The `<target>` path is **never** touched while a transfer is in flight. |
| **Resume via HTTP Range** | `Range: bytes=<offset>-` + server `206 Partial Content`. | When `<target>.part` exists, we `stat()` it for its size and send `Range: bytes=<size>-`. If the server returns `200 OK` (Range not honoured), we truncate and restart from 0. |
| **Hash-before-rename** | aria2 and most package managers verify checksum before committing. | After streaming completes, compute SHA-256 on `<target>.part`; on mismatch, quarantine to `<target>.part.corrupt-<timestamp>` and raise. |
| **Atomic publish** | POSIX `rename()` is atomic on the same filesystem; `os.replace()` normalises Windows/Linux semantics. | Final commit is a single `os.replace(target_part, target)`. This guarantees: (a) no reader ever sees a torn file; (b) a crash mid-transfer leaves only `.part`, which a subsequent run can resume. |
| **Idempotent re-entry** | aria2 resumes automatically if the control file is present. | On re-run, if the final `<target>` already exists with the expected SHA-256 **and** size, return `DownloadOutcome.ALREADY_VERIFIED` without opening a TCP socket. |

---

## 4. Fail-Safe Red Lines (Windows + Crash-Resistance)

Four non-negotiable guard rails enforced by both code and tests:

1. **Windows symlink privilege trap**: `os.symlink` on non-Admin non-Developer-Mode Windows raises `OSError(WinError 1314)`. The injector MUST catch `(OSError, NotImplementedError, AttributeError)` and silently degrade to hardlink → copy.
2. **No half-downloaded model pollution**: bytes never land at the final path; only `os.replace()` after a verified hash publishes the artifact.
3. **No destructive overwrite**: pre-existing but corrupted files are renamed to `.bak-<ts>` before replacement — never `os.remove()`.
4. **Strict idempotency**: second run emits zero `NETWORK` / `MUTATE` actions; enforced by `tests/test_idempotent_surgeon.py::TestSecondRunIsNoop`.

---

## 5. Concrete Code-Level Obligations (Traceability Matrix)

| Obligation | Enforced in |
|---|---|
| `PreflightReport` is the sole input contract | `idempotent_surgeon.IdempotentSurgeon.operate(report)` |
| Cache search before network | `asset_injector.AssetInjector.try_local_recovery()` |
| Symlink → hardlink → copy degradation | `asset_injector._inject_with_fallback()` |
| `.part` temp file + Range resume | `atomic_downloader.AtomicDownloader.fetch()` |
| SHA-256 verify before atomic rename | `atomic_downloader._verify_and_publish()` |
| `.bak-<ts>` quarantine, never delete | `asset_injector._quarantine_conflict()` |
| Second-run zero side-effect | `idempotent_surgeon._is_already_satisfied()` |
| Strongly-typed action outcomes | `idempotent_surgeon.ActionOutcome` (enum) |
