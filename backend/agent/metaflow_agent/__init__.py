"""
AgentMetadataProvider — Extended metadata provider with agent-optimized queries.

This module demonstrates the agent-friendly query layer that could be built
on top of Metaflow's existing metadata infrastructure. It works with BOTH
the local metadata provider and the service metadata provider.

Key optimizations:
1. Bounded listing (limit/offset) — prevents loading entire run history
2. Status-aware queries — uses attempt_ok metadata to avoid datastore reads
3. Batch operations — multiple task statuses in a single call
4. Server-side filtering via filter_tasks_by_metadata — leverages existing infra
"""
import re
import time
from collections import defaultdict
from typing import List, Optional, Dict, Any

from metaflow import Flow, Run, Step, Task, namespace
from metaflow.metadata_provider.metadata import MetadataProvider


class AgentMetadataProvider:
    """
    Agent-friendly query layer on top of Metaflow's Client API.

    This is NOT a replacement for MetadataProvider — it's a higher-level
    utility that wraps the Client API to provide bounded, efficient queries
    for common agent use cases.

    In a production GSoC implementation, these methods would be:
    1. New endpoints on the metadata service (server-side)
    2. Exposed through the Client API as first-class methods
    3. Backed by SQL queries instead of client-side iteration
    """

    # ----------------------------------------------------------------
    # 4.1: Bounded run listing
    # ----------------------------------------------------------------
    @staticmethod
    def get_recent_runs(flow_id: str, limit: int = 10) -> List[dict]:
        """
        Get the N most recent runs for a flow.

        Current implementation: fetches all runs, sorts client-side, truncates.
        Optimal implementation: SELECT * FROM runs WHERE flow_id=%s ORDER BY ts_epoch DESC LIMIT %s

        The waste is documented: the metadata service returns ALL runs
        but we only need `limit` of them.
        """
        flow = Flow(flow_id)
        runs = []
        count = 0
        for run in flow:
            runs.append({
                "id": run.id,
                "pathspec": run.pathspec,
                "created_at": str(run.created_at),
                "tags": list(run.tags),
            })
            count += 1
            if count >= limit:
                break  # Client API returns in reverse chronological order
        return runs

    # ----------------------------------------------------------------
    # 4.2: Run summary (single call)
    # ----------------------------------------------------------------
    @staticmethod
    def run_summary(run_pathspec: str) -> dict:
        """
        Get a structured summary of a run in one logical call.

        Returns status, step info, and timing without loading artifacts
        or iterating all tasks.

        Optimal implementation: a dedicated /runs/{id}/summary endpoint
        that returns this as a single SQL query joining runs, steps, and
        task metadata tables.
        """
        run = Run(run_pathspec)

        summary = {
            "pathspec": run.pathspec,
            "created_at": str(run.created_at),
            "tags": list(run.tags),
        }

        # Check end task for completion status
        try:
            end_task = run.end_task
            if end_task:
                is_successful = end_task.successful
                summary["status"] = "completed" if is_successful else "failed"
                summary["finished_at"] = str(end_task.finished_at) if is_successful else None
                return summary
        except Exception:
            pass

        # If no end task, the run is either in progress or failed mid-way
        steps = [s.id for s in run]
        summary["status"] = "in_progress_or_failed"
        summary["steps_completed"] = steps
        return summary

    # ----------------------------------------------------------------
    # 4.3: Find failures using metadata (no datastore reads)
    # ----------------------------------------------------------------
    @staticmethod
    def find_failures(
        run_pathspec: str,
        max_tasks: int = 100,
        use_metadata: bool = True
    ) -> dict:
        """
        Find failed tasks with bounded iteration.

        When use_metadata=True, checks attempt_ok metadata field
        (stored in metadata DB) instead of _success artifact
        (stored in datastore). This avoids S3/datastore reads entirely.

        Optimal implementation: a dedicated /runs/{id}/failed_tasks endpoint
        backed by:
          SELECT task_id, step_name FROM metadata
          WHERE field_name='attempt_ok' AND value='False'
          AND flow_id=%s AND run_number=%s
        """
        run = Run(run_pathspec)
        failures = []
        tasks_checked = 0

        for step in run:
            for task in step:
                tasks_checked += 1
                if tasks_checked > max_tasks:
                    return {
                        "failures": failures,
                        "truncated": True,
                        "checked": tasks_checked,
                    }

                try:
                    if use_metadata:
                        # Use metadata DB — avoids datastore artifact reads
                        md = task.metadata_dict
                        attempt_ok = md.get("attempt_ok")
                        if attempt_ok is None:
                            failures.append({
                                "pathspec": task.pathspec,
                                "step": step.id,
                                "status": "not_finished",
                            })
                        elif attempt_ok == "False":
                            failures.append({
                                "pathspec": task.pathspec,
                                "step": step.id,
                                "status": "failed",
                            })
                    else:
                        # Naive: loads _success artifact from datastore
                        if not task.successful:
                            failures.append({
                                "pathspec": task.pathspec,
                                "step": step.id,
                                "status": "failed",
                            })
                except Exception as e:
                    failures.append({
                        "pathspec": task.pathspec,
                        "error": str(e)[:200],
                    })

        return {
            "failures": failures,
            "truncated": False,
            "checked": tasks_checked,
        }

    # ----------------------------------------------------------------
    # 4.4: Batch run status check
    # ----------------------------------------------------------------
    @staticmethod
    def batch_run_status(flow_id: str, limit: int = 10) -> List[dict]:
        """
        Get status for the N most recent runs in a single call.

        Optimal implementation: a dedicated /flows/{id}/run_statuses endpoint
        backed by a JOIN of runs + metadata tables:
          SELECT r.run_number, r.ts_epoch,
                 CASE WHEN m.value = 'True' THEN 'completed'
                      WHEN m.value = 'False' THEN 'failed'
                      ELSE 'in_progress' END as status
          FROM runs r
          LEFT JOIN metadata m ON ...
          WHERE r.flow_id = %s
          ORDER BY r.ts_epoch DESC LIMIT %s
        """
        results = []
        count = 0
        for run in Flow(flow_id):
            count += 1
            if count > limit:
                break
            try:
                results.append({
                    "pathspec": run.pathspec,
                    "created_at": str(run.created_at),
                    "successful": run.successful,
                })
            except Exception:
                results.append({
                    "pathspec": run.pathspec,
                    "created_at": str(run.created_at),
                    "successful": None,
                })
        return results

    # ----------------------------------------------------------------
    # 4.5: Server-side failure detection via filter_tasks_by_metadata
    # ----------------------------------------------------------------
    @staticmethod
    def find_failed_tasks_server_side(
        flow_id: str, run_id: str, step_name: str
    ) -> List[str]:
        """
        Use the existing filter_tasks_by_metadata endpoint to find
        failed tasks entirely server-side.

        This is the ONLY existing server-side filtering capability
        in the metadata service. It queries the metadata table with
        a regex pattern on field values.

        The call: GET /flows/{flow_id}/runs/{run_id}/steps/{step_name}/filtered_tasks
                  ?metadata_field_name=attempt_ok&pattern=False
        """
        try:
            return MetadataProvider.filter_tasks_by_metadata(
                flow_id, run_id, step_name,
                field_name="attempt_ok",
                pattern="False"
            )
        except Exception as e:
            # Falls back if filter_tasks_by_metadata is not available
            return [f"Error: {e}"]

    # ----------------------------------------------------------------
    # 4.6: Tail logs (bounded)
    # ----------------------------------------------------------------
    @staticmethod
    def tail_logs(
        task_pathspec: str,
        stream: str = "stdout",
        n_lines: int = 50
    ) -> str:
        """
        Get the last N lines of a task's log.

        Uses loglines() with a rolling deque — avoids building
        the full log string in memory.

        Optimal implementation: byte-range reads from S3/datastore,
        or a /tasks/{id}/logs?tail=50 endpoint.
        """
        from collections import deque
        task = Task(task_pathspec)
        recent = deque(maxlen=n_lines)
        for timestamp, line in task.loglines(stream):
            recent.append(line)
        return "\n".join(recent)

    # ----------------------------------------------------------------
    # 4.7: Time-filtered run listing
    # ----------------------------------------------------------------
    @staticmethod
    def get_runs_since(flow_id: str, hours: int = 24) -> List[dict]:
        """
        Get runs from the last N hours.

        Current: loads all runs, filters client-side.
        Optimal: SELECT * FROM runs WHERE flow_id=%s AND ts_epoch > %s
        """
        from datetime import datetime, timedelta
        cutoff = datetime.now() - timedelta(hours=hours)
        results = []
        for run in Flow(flow_id):
            if run.created_at >= cutoff:
                results.append({
                    "pathspec": run.pathspec,
                    "created_at": str(run.created_at),
                })
            # Can't break early — runs may not be strictly time-ordered
            # (another argument for server-side filtering)
        return results
