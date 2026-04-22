# SESSION-141: System Purge & Observability Research Notes

## 1. Blackbox Flight Recorder & Global Exception Hook

### Key Pattern: `sys.excepthook`
- Override `sys.excepthook` at process entry point (first line)
- Handler signature: `(exc_type, exc_value, exc_traceback)`
- MUST skip `KeyboardInterrupt` via `issubclass(exc_type, KeyboardInterrupt)` — delegate to `sys.__excepthook__`
- Use `logger.critical("Unhandled exception", exc_info=(exc_type, exc_value, exc_traceback))` to write full traceback to log file
- The hook itself MUST be wrapped in try-except to prevent secondary crash (deadlock red line)
- For threading: patch `threading.Thread.__init__` to wrap `run()` with excepthook forwarding

### Industrial Best Practices
- Aviation "black box" principle: always write crash data before death
- Double-fault protection: if log write fails (disk full), silently degrade via bare `try-except` with `sys.stderr.write()`
- Never let the logging mechanism itself cause a hang or secondary crash

## 2. Automated GC & Eager Pruning

### Two-Level Cleanup Architecture
- **Level 1 — Cold GC (startup)**: Scan workspace for stale artifacts older than TTL (7 days)
  - Target: `.part` files, `temp/` caches, orphaned intermediate files
  - Use `os.stat().st_mtime` for age check, `os.remove()` / `shutil.rmtree()` for deletion
  - MUST protect: `knowledge/active/`, `blueprints/`, `outputs/`, Elite markers

- **Level 2 — Hot Pruning (in-flight)**: During evolution loops
  - After parameters/features extracted from generation N, immediately delete generation N's large artifacts
  - Keep only lightweight JSON gene records
  - Use `os.remove()` for individual files, `shutil.rmtree()` for directories
  - CRITICAL: Must verify "next-gen params safely in memory" BEFORE deleting old files (temporal safety)

### Safe Deletion Patterns
- `pathlib.Path.unlink(missing_ok=True)` for individual files
- `shutil.rmtree(path, ignore_errors=True)` for directory trees
- Always log what was deleted for audit trail

## 3. Log Multiplexing & Rotation

### TimedRotatingFileHandler
- `logging.handlers.TimedRotatingFileHandler(filename, when='midnight', backupCount=7)`
- `backupCount=7` ensures only last 7 days retained
- Daily rotation at midnight
- File format: `app.log`, rotated to `app.log.2026-04-21`, etc.

### Multiplexing Strategy
- Root logger gets file handler (DEBUG level) — captures everything
- Console handler (WARNING or higher) — keeps terminal clean
- Subprocess stdout/stderr redirected to structured log files
- TUI remains "极度清爽" (extremely clean)

## 4. Centralized Configuration

### Pattern: Dataclass + Environment Override
- Single `settings.py` module with frozen dataclass
- All magic numbers extracted as named constants with documentation
- `.env` file support via `os.environ.get()` with typed defaults
- No external dependency needed (avoid pydantic-settings for minimal footprint)
- Categories: network (retry, timeout), sandbox, evolution, GC, logging

## 5. Dependency Diet

### Audit Strategy
- Scan all `import` statements across codebase
- Identify unused imports and dead modules
- Ensure heavy libraries (torch, tensorflow) are never globally imported
- Use lazy imports (`importlib`) for optional heavy dependencies
- Clean up abandoned scripts in `scripts/` directory
