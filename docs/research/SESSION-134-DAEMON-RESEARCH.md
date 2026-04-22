# SESSION-134 Research Anchors — P0-SESSION-131-DAEMON-LAUNCHER

> This document archives the binding research that governs the design of
> `mathart/workspace/daemon_supervisor.py` and
> `mathart/workspace/launcher_facade.py`. Every architectural decision in
> those modules MUST be traceable to one of the anchors below.

## Anchor 1 — Kubernetes Pod Probes (Readiness / Liveness / Startup)

Source: *Kubernetes Documentation — Pod Lifecycle / Container probes* ([kubernetes.io](https://kubernetes.io/docs/concepts/workloads/pods/pod-lifecycle/#container-probes)).

Kubernetes distinguishes three probe categories that map directly onto our
supervisor state machine:

| Probe | Purpose in K8s | Our analogue |
|-------|---------------|--------------|
| **Startup probe** | Protects slow-booting apps; all other probes are disabled until it succeeds. | `STARTING → READY` transition is gated by the startup probe (`/system_stats`). |
| **Readiness probe** | Decides whether traffic is routed to the pod. Default is `Failure` before the first success. | Only after readiness succeeds do we permit `MassProductionFactory.run()` to dispatch workflows. |
| **Liveness probe** | Kills and restarts an unhealthy container. | `CRASHED → RESTARTING` transition; supervisor enforces a restart budget per minute. |

Binding consequences for our code:

- Default state before the first successful probe is `Failure`, so the supervisor must NEVER treat `STARTING` as ready.
- Probes use HTTP `GET` with success defined as `200 ≤ status < 400`.
- Probe polling must respect `periodSeconds` and a `failureThreshold`; we implement this as **exponential backoff** bounded by a ceiling (PM2-style).

## Anchor 2 — PM2 / systemd Process Supervision

Two principles govern our supervisor's I/O discipline:

1. **Pipe drain discipline** — if the child saturates the OS pipe buffer (typical Linux default ≈ 64 KiB) and nobody is reading, the child blocks on `write()` and the whole service appears hung. PM2 and systemd both consume stdout/stderr in dedicated reader threads.
2. **Streaming log triage** — PM2 parses known crash signatures (e.g. `CUDA out of memory`) and emits structured events. We mirror this with a small regex table applied line-by-line as logs stream.

Binding consequences:

- We MUST spawn two daemon threads (`_stdout_pump`, `_stderr_pump`) that continuously drain the child pipes into a bounded queue.
- Reader threads must be `daemon=True` so Python does not wait for them on interpreter exit — the lifetime hook handles the subprocess itself.

## Anchor 3 — POSIX Process Groups & Windows Job Objects

Source: *Python stdlib — subprocess* ([docs.python.org](https://docs.python.org/3/library/subprocess.html#subprocess.Popen)).

- **POSIX**: pass `start_new_session=True` (equivalent to `preexec_fn=os.setsid`). The child becomes the leader of a new session + process group; killing the whole tree is a single `os.killpg(pgid, SIGTERM)`.
- **Windows**: pass `creationflags=subprocess.CREATE_NEW_PROCESS_GROUP`. This is the prerequisite for `os.kill(pid, signal.CTRL_BREAK_EVENT)` to deliver a group-wide signal.
- **Cross-platform guard**: on Windows, `SIGTERM` and `SIGKILL` are not meaningful for console applications. The supervisor falls back to `Popen.terminate()` (which maps to `TerminateProcess`) and — after a grace window — `Popen.kill()`.

Binding consequences:

- The supervisor's `stop()` method must: (1) try a graceful signal appropriate to the platform, (2) wait `graceful_timeout` seconds, (3) escalate to `kill()` which guarantees termination of the leader process (and, if the group-spawn succeeded, the whole tree).
- The supervisor must register an `atexit` hook and install `SIGINT` / `SIGTERM` handlers to guarantee cleanup on any exit path from the Python main process. On Windows we additionally install a `signal.SIGBREAK` handler when available.

## Derived Red Lines (enforced by tests)

1. **Zombie-free exit** — after `supervisor.stop()`, the child PID must not be present in `psutil.pids()`. The supervisor's test harness spawns a mock ComfyUI and asserts the PID disappears even when the test is torn down via exception.
2. **No deadlock on verbose children** — the supervisor's log pump uses an unbounded `queue.Queue` (consumer-side bounded) so a chatty child cannot block the producer.
3. **Attach-over-Launch** — when port 8188 is already bound, the supervisor issues a probe before deciding to fail. If the foreign process responds to `/system_stats` with HTTP 200, the supervisor transitions straight to `ATTACHED` and never spawns its own child.
4. **Facade atomicity** — `LauncherFacade.start()` is idempotent: calling it twice on the same instance does NOT create a second subprocess.
