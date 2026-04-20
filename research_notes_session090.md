# SESSION-090 Research Notes — Backend Hot-Reload Ecosystem (P1-MIGRATE-4)

## Industrial & Academic Reference Synthesis

### 1. Erlang/OTP Hot Code Swapping — Zero-Downtime Golden Standard

Erlang/OTP's hot code replacement is the industry gold standard for zero-downtime code updates. The core philosophy is **strict isolation of code state from singleton runtime state**. Key principles enforced in our implementation:

- **Two-version coexistence**: Erlang maintains at most two versions of a module simultaneously (current + old). When a new version is loaded, the old version is marked for garbage collection. Only processes actively executing old code retain references; new calls route to the new version.
- **State protection during reload**: The global process registry and supervision trees are NEVER cleared during a code swap. Only the target module's code is replaced. This maps directly to our requirement: `BackendRegistry._backends` must perform **targeted replacement** of a single entry, never `clear()`.
- **Atomic swap semantics**: The `code_change/3` callback in `gen_server` ensures state migration is atomic. Our `reload(name)` must similarly be atomic: unregister → reimport → re-register in a single critical section.

**Enforced Implementation Consequence**: `BackendRegistry.unregister(name)` + `reload(name)` must be **surgically targeted** — only the named backend is evicted and replaced. All other entries remain untouched.

### 2. Eclipse OSGi Dynamic Module System — Lifecycle-Strict Plugin Management

OSGi defines a strict bundle lifecycle: INSTALLED → RESOLVED → STARTING → ACTIVE → STOPPING → UNINSTALLED. Key principles:

- **Atomic unregister-before-reload**: Before a new bundle version can be loaded, the old version's services must be atomically unregistered from the service registry. This prevents "state stickiness" where consumers hold references to stale service instances.
- **Dependency graph awareness**: OSGi tracks inter-bundle dependencies. When bundle A depends on bundle B, and B is reloaded, A's service references are invalidated and must be re-resolved. Our `BackendRegistry.resolve_dependencies()` already provides this topology — hot-reload must respect it.
- **Version isolation**: Each bundle version has its own classloader. In Python terms, this means `importlib.reload()` must deeply refresh `sys.modules` to prevent old class definitions from lingering.

**Enforced Implementation Consequence**: The reload sequence must be: (1) atomically remove from `_backends`, (2) deep-clean `sys.modules` for the target module, (3) reimport and re-trigger `@register_backend`, (4) verify the new class `id()` differs from the old one.

### 3. Unity/Unreal Engine Domain Reloading / Live Coding

Game engines implement hot-reload through domain reloading (Unity) and live coding (Unreal):

- **Safe Point execution**: Unity's domain reload only occurs at a "safe point" in the main loop — never mid-frame. This prevents pipeline rupture. Our file watcher must use **debouncing** (300-500ms) to ensure files are fully written before triggering reload.
- **Serialization-based state preservation**: Unity serializes MonoBehaviour state before domain unload and deserializes after reload. Our registry entries are stateless class references (not instances), so this is simpler — we just need to ensure the singleton registry dict survives.
- **Independent thread for compilation**: Unreal's Live Coding compiles on a background thread and patches the vtable on the main thread. Our watcher runs on a daemon thread; the actual `importlib.reload()` should also be thread-safe via a lock.

**Enforced Implementation Consequence**: File watcher daemon thread + debounce timer + threading.Lock for registry mutation.

### 4. Python `watchdog` + `importlib.reload` Best Practices

- **`watchdog` event coalescing**: IDE saves often trigger multiple filesystem events (CREATE → MODIFY → MODIFY). The watcher must implement a debounce window (300-500ms) to coalesce these into a single reload trigger.
- **Deep `sys.modules` cleanup**: `importlib.reload(module)` re-executes the module's top-level code but does NOT update references held by other modules. To prevent "zombie class" references, we must: (1) pop the module from `sys.modules`, (2) call `importlib.reload()`, (3) verify the new class object has a different `id()`.
- **`_builtins_loaded` flag management**: The current `BackendRegistry._builtins_loaded` flag prevents re-discovery. Hot-reload must NOT reset this flag globally — it must only affect the targeted module.

**Enforced Implementation Consequence**: Per-module reload with `sys.modules` cleanup, NOT global `_builtins_loaded` reset.

## Anti-Pattern Guards (SESSION-090 Red Lines)

### 🚫 Zombie Reference Trap
`importlib.reload` creates new class objects but old instances survive in memory. After reload, `id(OldClass) != id(NewClass)` but any variable holding `OldClass` still points to the ghost. Test MUST assert `id()` change.

### 🚫 State Wipeout Trap
NEVER call `BackendRegistry._backends.clear()` or `BackendRegistry.reset()` during single-backend reload. This would destroy all other backends mid-flight.

### 🚫 Blocking & Debounce Trap
File watcher MUST run on daemon thread. Debounce window MUST be 300-500ms. NEVER reload on partial file write (would cause SyntaxError).

## Implementation Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                    BackendRegistry (Singleton)                │
│                                                              │
│  + unregister(name) ─── atomic pop from _backends            │
│  + reload(name) ─────── unregister → sys.modules cleanup     │
│                          → importlib.reload → re-register    │
│  + _reload_lock ──────── threading.Lock for thread safety    │
│                                                              │
│  ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌─────────┐       │
│  │backend_A│  │backend_B│  │backend_C│  │ ... N   │       │
│  └─────────┘  └─────────┘  └─────────┘  └─────────┘       │
└──────────────────────────────────────────────────────────────┘
         ▲                                    ▲
         │ targeted reload                    │ untouched
         │                                    │
┌──────────────────────────────────────────────────────────────┐
│              BackendFileWatcher (Daemon Thread)               │
│                                                              │
│  watchdog.Observer → FileSystemEventHandler                  │
│  - Monitors discover() scan paths dynamically                │
│  - Debounce timer (400ms default)                            │
│  - Maps .py file → backend module → registry.reload(name)   │
│  - start() / stop() lifecycle                                │
└──────────────────────────────────────────────────────────────┘
```
