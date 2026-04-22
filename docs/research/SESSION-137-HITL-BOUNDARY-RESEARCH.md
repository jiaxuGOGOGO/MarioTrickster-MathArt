# SESSION-137 HITL Boundary Gateway Research

**Author:** Manus AI  
**Scope:** This document translates external industrial and academic references into binding engineering rules for landing `P0-SESSION-134-HITL-BOUNDARY-GATEWAY` inside `MarioTrickster-MathArt`.

## Executive Synthesis

This session targets a specific class of failure that prior automation layers still handled too optimistically: **physical boundary failures**. These are not ordinary software exceptions. They include operating-system privilege barriers, GPU/driver heterogeneity, network blockage, and security software interference. Across the external references, the consistent conclusion is that once automation reaches such boundaries, the correct system behavior is **bounded autonomy rather than blind continuation**. Human oversight is not an afterthought but a first-class control surface in high-risk or high-cost contexts [1] [2].

For this repository, that means the infrastructure path must stop treating every failure as an implementation detail to be silently retried or automatically downgraded. When a symlink fails on Windows, when a required CUDA environment is absent, when repeated download timeouts imply real network blockage, or when antivirus false positives are likely, the system must convert raw failure into **structured, actionable, operator-readable guidance**. That principle is also consistent with the current architecture of the project: Radar remains responsible for read-only environment truth, Surgeon remains responsible for safe reconciliation, and the launcher path must refuse to cross into daemon start when the environment has already declared a hard boundary.

## External Findings and Their Binding Implications

| External source | Key statement or finding | Binding implication for this repository |
|---|---|---|
| Microsoft Learn — Create symbolic links | Windows treats symbolic-link creation as the `SeCreateSymbolicLinkPrivilege` user right and grants it to administrators by default; the document explicitly warns that this privilege should be limited to trusted users [3]. | A Windows symlink failure is a real permission boundary, not a cosmetic warning. For large assets, the injector must not silently fall through to multi-hundred-megabyte copying without explicit user choice. |
| Python Standard Library — `urllib.request` | `getproxies()` scans `<scheme>_proxy` environment variables, and `ProxyHandler` uses them by default [4]. | The correct recovery path after repeated download timeouts is to ask the user for proxy/mirror intent, persist the proxy locally, inject proxy environment variables, and only then retry under bounded control. |
| Microsoft Defender docs | Microsoft officially supports folder, file, and process exclusions, and PowerShell exposes `Add-MpPreference -ExclusionPath` as the standard path-based exclusion command [5] [6]. | The project should print human-copyable whitelist guidance, but it must never modify Defender state on the user’s behalf. |
| NVIDIA CUDA Installation Guide for Windows | CUDA requires a CUDA-capable GPU, supported Windows version, and correct installation of both driver and toolkit [7]. | If a selected mode requires GPU execution, missing NVIDIA/CUDA prerequisites must block download/startup early, before heavyweight artifacts are fetched. |
| HITL systematic review | HITL is especially important in environments with high error cost and explainability requirements; modern design is shifting from replacing humans to augmenting them through oversight and trust calibration [2]. | The repository should represent hard-boundary failures as human-handoff events with typed semantics and structured resolution options, not as opaque stack traces or infinite retries. |

## Architectural Translation

The research points toward a unified design rule: **automated infrastructure must remain aggressive inside software-only recovery space, but conservative at physical and policy boundaries**. That distinction is crucial. Retrying a socket once or twice, resolving a valid symlink, or resuming a partial `.part` file are ordinary automation tasks. By contrast, Windows privilege limitations, missing NVIDIA capability, sustained network blockage, and antivirus policy are not automation-owned facts. They belong to the operator’s environment and therefore require explicit human acknowledgement.

This session therefore adopts four implementation laws.

| Law | Description | Code-level consequence |
|---|---|---|
| HITL law | Hard-boundary failures must produce a human-handoff contract rather than a raw crash. | Introduce a strongly typed `ManualInterventionRequiredError` carrying machine-readable options and human-readable guidance. |
| Graceful degradation law | A failed automatic path must degrade to guided choice, not to uncontrolled fallback. | Replace silent copy fallback and unlimited network retry behavior with wizard-driven decision branches. |
| Least-astonishment law | High-cost side effects must never occur implicitly. | Large file copy after symlink failure must require explicit opt-in if file size crosses the configured threshold. |
| Layer-discipline law | Boundary detection belongs to infrastructure layers, not business pipelines. | Keep detection inside Radar, Surgeon, Downloader, or their shared gateway helpers; do not leak OS probing into unrelated production/evolution code paths. |

## Design Directives for the Four Gateways

### 1. UAC / Symlink Guard

Windows symbolic-link failure is explicitly privilege-sensitive [3]. Therefore, when the asset injector encounters a Windows-style permission failure such as `WinError 1314`, and the source asset exceeds the threshold defined by the task, the injector must **stop before copy fallback**. The system should raise a structured manual-intervention exception offering three explicit branches: rerun elevated, enable Developer Mode / symlink privilege path, or accept a slow full copy. This preserves operator agency and avoids violating the Principle of Least Astonishment through silent disk-heavy mutation.

### 2. Driver & CUDA Guard

NVIDIA’s installation guide makes the prerequisites explicit: a CUDA-capable GPU and the correct driver/toolkit stack are required for CUDA execution [7]. Therefore, GPU capability checks should happen **before** download and daemon-start stages for modes that genuinely require GPU. The guard should not block research-only or dry-run flows that are explicitly CPU-safe. In other words, the repository should discriminate between *GPU-required operational lanes* and *GPU-optional research lanes*, using `SessionContext` rather than a global hard stop.

### 3. Network & Proxy Guard

The Python networking stack already recognizes proxy configuration through standard environment variables [4]. Therefore, the downloader should treat repeated transport timeout as a bounded failure: after the configured retry ceiling, it should stop and hand control back to the operator. The operator should be able to choose a local proxy, a mirror source, or exit cleanly. Any chosen proxy must be persisted via the existing configuration manager rather than hidden in ad-hoc shell state.

### 4. Anti-Virus / Defender Guard

Microsoft’s documentation confirms that exclusions are legitimate administrative configuration, including through `Add-MpPreference -ExclusionPath` [5] [6]. However, because changing antivirus policy is a system-level security action, the application itself must never perform it implicitly. The correct product behavior is a conspicuous warning at wizard entry and in preflight output, paired with copyable reference commands and clear directory placeholders for the project root and ComfyUI root.

## Implementation Constraints Derived from the Research

| Constraint | Why it follows from the research |
|---|---|
| No raw `print()/input()` forks for boundary prompts | Human handoff must be consistent and legible; fragmented ad-hoc I/O erodes trust calibration and operator understanding [2]. |
| No forced registry or firewall mutation | Physical/system policy belongs to the operator domain, not to bounded automation. |
| No infinite retry loops | Graceful degradation requires returning control after bounded attempts, not hiding failure behind endless repetition. |
| Strongly typed manual boundary exceptions | HITL is not just UX copy; it is an architectural state transition that other layers and tests must detect deterministically. |

## Engineering Conclusion

The code landing for `P0-SESSION-134-HITL-BOUNDARY-GATEWAY` should be evaluated by one core question: **when reality stops being software-shaped, does the system become more honest and more operator-guided, or does it become more opaque and more aggressive?** The external references clearly support the former. For this repository, success means that the infrastructure stack becomes safer for non-technical artists precisely because it becomes *less* willing to guess at privileged, hardware-dependent, or policy-sensitive recovery paths.

## References

[1]: https://ntrs.nasa.gov/api/citations/20240008246/downloads/Human%20In%20The%20Loop%20Simulation.pdf "A Human-In-The-Loop Simulation for Urban Air Mobility in the Terminal Area — NASA / San Jose State University"
[2]: https://www.mdpi.com/1099-4300/28/4/377 "Human-in-the-Loop Artificial Intelligence: A Systematic Review of Concepts, Methods, and Applications"
[3]: https://learn.microsoft.com/en-us/previous-versions/windows/it-pro/windows-10/security/threat-protection/security-policy-settings/create-symbolic-links "Create symbolic links - Windows 10 | Microsoft Learn"
[4]: https://docs.python.org/3/library/urllib.request.html "urllib.request — Extensible library for opening URLs — Python documentation"
[5]: https://learn.microsoft.com/en-us/defender-endpoint/configure-exclusions-microsoft-defender-antivirus "Configure custom exclusions for Microsoft Defender Antivirus - Microsoft Learn"
[6]: https://learn.microsoft.com/en-us/powershell/module/defender/add-mppreference?view=windowsserver2025-ps "Add-MpPreference (Defender) | Microsoft Learn"
[7]: https://docs.nvidia.com/cuda/cuda-installation-guide-microsoft-windows/ "CUDA Installation Guide for Microsoft Windows — NVIDIA"
