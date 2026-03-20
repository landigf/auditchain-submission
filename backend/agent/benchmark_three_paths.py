#!/usr/bin/env python3
"""
BenchmarkThreePaths: Compare ALL three approaches to finding failed tasks.

    start ─┬─ path_a_naive        ─┬─ compare ─ end
           ├─ path_b_ui_backend   ─┤
           └─ path_c_smart_meta   ─┘

Path A: Naive Client API (today) — iterate tasks, fetch _success artifact each
Path B: UI Backend Service — rich query, status in response
Path C: Smart Metadata (the real fix) — filter_tasks_by_metadata + _limit/_order

All three query the SAME Postgres database.

Usage:
    export METAFLOW_SERVICE_URL=http://localhost:8080
    export METAFLOW_DEFAULT_METADATA=service
    export METAFLOW_DEFAULT_DATASTORE=local
    python3 benchmark_three_paths.py run
"""

from metaflow import FlowSpec, step

TARGET_FLOW = "ForeachFlow"
UI_BACKEND = "http://localhost:8083"


class BenchmarkThreePaths(FlowSpec):

    @step
    def start(self):
        """Find the latest ForeachFlow run to analyze."""
        import requests
        resp = requests.get(f"{UI_BACKEND}/api/flows/{TARGET_FLOW}/runs",
                            params={"_limit": 1, "_order": "-run_number"})
        self.target_run = resp.json()["data"][0]["run_number"]
        print(f"Target: {TARGET_FLOW}/{self.target_run}")
        self.next(self.path_a_naive, self.path_b_ui_backend, self.path_c_smart_meta)

    # ── Path A: Naive Client API (what exists today) ────────────────

    @step
    def path_a_naive(self):
        """
        NAIVE: Iterate every task, fetch _success artifact per task.
        This is what agents do today with the Client API.
        """
        import time
        from metaflow import Flow, namespace
        from metaflow.plugins.metadata_providers.service import ServiceMetadataProvider

        http_log = []
        _orig = ServiceMetadataProvider._request.__func__

        @classmethod
        def _counting(cls, monitor, path, method, data=None,
                      retry_409_path=None, return_raw_resp=False):
            http_log.append(path)
            return _orig(cls, monitor, path, method, data,
                         retry_409_path, return_raw_resp)

        ServiceMetadataProvider._request = _counting
        namespace(None)
        http_log.clear()
        t0 = time.time()

        flow = Flow(TARGET_FLOW)
        latest = next(iter(flow))
        failures = []
        for step_obj in latest:
            for task in step_obj:
                if not task.successful:
                    failures.append(task.pathspec)

        self.elapsed_ms = (time.time() - t0) * 1000
        self.http_calls = len(http_log)
        self.failures = sorted(failures)
        self.label = "A: Naive Client API"
        self.description = "Iterate all tasks, fetch _success artifact per task"

        ServiceMetadataProvider._request = classmethod(_orig)
        print(f"Path A: {self.http_calls} calls, {self.elapsed_ms:.0f}ms, "
              f"found {len(self.failures)} failures")
        self.next(self.compare)

    # ── Path B: UI Backend (the shortcut we discovered) ─────────────

    @step
    def path_b_ui_backend(self):
        """
        UI BACKEND: Status included in response, 2 calls.
        Requires UI Backend service to be deployed.
        """
        import time
        import requests

        t0 = time.time()
        calls = 0

        resp = requests.get(f"{UI_BACKEND}/api/flows/{TARGET_FLOW}/runs",
                            params={"_limit": 1, "_order": "-run_number"})
        calls += 1
        run_number = resp.json()["data"][0]["run_number"]

        # Get all tasks for process step
        resp = requests.get(
            f"{UI_BACKEND}/api/flows/{TARGET_FLOW}/runs/{run_number}"
            f"/steps/process/tasks", params={"_limit": 200})
        calls += 1
        tasks = resp.json().get("data", [])
        failures = [f"{TARGET_FLOW}/{run_number}/process/{t['task_id']}"
                    for t in tasks if t.get("status") == "failed"]

        self.elapsed_ms = (time.time() - t0) * 1000
        self.http_calls = calls
        self.failures = sorted(failures)
        self.label = "B: UI Backend"
        self.description = "Requires extra service (port 8083)"

        print(f"Path B: {self.http_calls} calls, {self.elapsed_ms:.0f}ms, "
              f"found {len(self.failures)} failures")
        self.next(self.compare)

    # ── Path C: Smart Metadata (the real fix) ───────────────────────

    @step
    def path_c_smart_meta(self):
        """
        SMART METADATA: Use filter_tasks_by_metadata (already in the
        metadata service since v2.5.0) to find failures in ONE call.
        No UI Backend needed. No artifact fetching. No unpickling.
        Works with every deployment that has the metadata service.
        """
        import time
        from metaflow import namespace
        from metaflow.plugins.metadata_providers.service import ServiceMetadataProvider

        http_log = []
        _orig = ServiceMetadataProvider._request.__func__

        @classmethod
        def _counting(cls, monitor, path, method, data=None,
                      retry_409_path=None, return_raw_resp=False):
            http_log.append(path)
            return _orig(cls, monitor, path, method, data,
                         retry_409_path, return_raw_resp)

        ServiceMetadataProvider._request = _counting
        namespace(None)
        http_log.clear()
        t0 = time.time()

        run_id = str(self.target_run)

        # Get steps first (1 call)
        from metaflow import Run
        run = Run(f"{TARGET_FLOW}/{run_id}")
        step_names = [s.id for s in run]

        # For each step, use filter_tasks_by_metadata to find failures (1 call per step)
        failures = []
        for step_name in step_names:
            failed_tasks = ServiceMetadataProvider.filter_tasks_by_metadata(
                TARGET_FLOW, run_id, step_name, "attempt_ok", "False")
            failures.extend(failed_tasks)

        self.elapsed_ms = (time.time() - t0) * 1000
        self.http_calls = len(http_log)
        self.failures = sorted(failures)
        self.label = "C: Smart Metadata"
        self.description = "filter_tasks_by_metadata — no UI Backend needed"

        ServiceMetadataProvider._request = classmethod(_orig)
        print(f"Path C: {self.http_calls} calls, {self.elapsed_ms:.0f}ms, "
              f"found {len(self.failures)} failures")
        self.next(self.compare)

    # ── Join: compare all three ─────────────────────────────────────

    @step
    def compare(self, inputs):
        """Compare results from all three paths."""
        self.results = []
        for inp in inputs:
            self.results.append({
                "label": inp.label,
                "description": inp.description,
                "http_calls": inp.http_calls,
                "elapsed_ms": round(inp.elapsed_ms),
                "failures": inp.failures,
            })

        self.results.sort(key=lambda r: r["http_calls"], reverse=True)

        # Verify all paths found the same failures
        failure_sets = [set(r["failures"]) for r in self.results]
        self.all_agree = all(f == failure_sets[0] for f in failure_sets)

        self.target_run = inputs[0].target_run
        self.next(self.end)

    @step
    def end(self):
        """Build the final report as an artifact (visible in UI)."""
        a = next(r for r in self.results if "Naive" in r["label"])
        c = next(r for r in self.results if "Smart" in r["label"])
        b = next(r for r in self.results if "UI" in r["label"])

        lines = []
        lines.append("")
        lines.append("=" * 70)
        lines.append("  THREE-PATH BENCHMARK: Finding Failed Tasks")
        lines.append("=" * 70)
        lines.append("")
        lines.append(f"  Target: {TARGET_FLOW}/{self.target_run}")
        lines.append(f"  All paths agree: {self.all_agree}")
        lines.append(f"  Failed tasks: {self.results[0]['failures']}")
        lines.append("")
        lines.append(f"  {'Path':<28} {'Calls':>6} {'Time':>8}  Notes")
        lines.append(f"  {'_' * 28} {'_' * 6} {'_' * 8}  {'_' * 30}")
        for r in self.results:
            lines.append(f"  {r['label']:<28} {r['http_calls']:>6} "
                         f"{r['elapsed_ms']:>7}ms  {r['description']}")
        lines.append("")
        lines.append(f"  Smart Meta vs Naive:  "
                     f"{a['http_calls'] - c['http_calls']} fewer calls, "
                     f"{a['elapsed_ms'] / max(c['elapsed_ms'], 1):.1f}x faster")
        lines.append(f"  Smart Meta vs UI:     "
                     f"{abs(c['http_calls'] - b['http_calls'])} "
                     f"{'more' if c['http_calls'] > b['http_calls'] else 'fewer'} calls, "
                     f"no extra service dependency")
        lines.append("")
        lines.append("  Path C uses filter_tasks_by_metadata (service v2.5.0+)")
        lines.append("  No UI Backend needed. Works on every deployment.")
        lines.append("")

        # Store as artifact so the UI Artifacts tab can show it
        self.report = "\n".join(lines)

        # Also print to terminal
        print(self.report)


if __name__ == "__main__":
    BenchmarkThreePaths()
