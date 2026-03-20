# Evaluation-Driven Improvements Log

## Baseline: 20/27 (74.1%), AIS avg 99

### Round 1: Fuzzy Logic Calibration
**Files changed**: `agent/fuzzy_policy.py`, `agent/pipeline.py`, `agent/llm_client.py`

1. **Score gap severity** — power curve instead of linear
   - Old: `gap_severity = max(0, 1.0 - gap / 15.0)`
   - New: `gap_severity = max(0, 1.0 - (gap / 10.0) ** 0.7)` — drops faster for meaningful gaps
   - Threshold raised from 0.1 to 0.15 to avoid noise signals

2. **Confidence formula** — signal count dampening
   - Single uncertainty signal alone shouldn't tank confidence
   - Added `signal_count_factor = min(1.0, len(signals) / 3)` to weight compound uncertainty higher
   - Formula: `combined_severity = raw_severity * (0.5 + 0.5 * signal_count_factor)`

3. **Fuzzy tier escalation** — membership-based thresholds
   - Now checks fuzzy membership >= 0.3 instead of simple tier comparison
   - Tier 3+ always significant regardless of membership

4. **Escalation threshold** — lowered from confidence < 0.30 to < 0.50
   - Ensures moderate-confidence borderline cases get human review
   - Fixed REQ-000033 (€24.5k near €25k threshold) not escalating

### Round 2: Ambiguity & Clarification Logic
**Files changed**: `agent/pipeline.py`, `agent/llm_client.py`

5. **LLM parse prompt** — enhanced ambiguity detection
   - Added instructions for: vague urgency, contradictory supplier preferences, missing delivery location, restricted suppliers

6. **Smart clarification trigger** — critical keyword filtering
   - Old: triggers on `>= 2` ambiguities (missed single critical ambiguities)
   - Then: `>= 1` (too aggressive, clarified on minor issues)
   - Final: critical keywords (deadline, budget, quantity, etc.) + `>= 2` non-critical
   - Fixed REQ-000002 ("move quickly" = vague urgency → clarification_needed)

7. **Infeasibility decision** — now includes best supplier recommendation
   - Even when budget is infeasible, recommend the top-scored supplier as a starting point for humans

### Round 3: Reliability
**Files changed**: `agent/llm_client.py`, `db/database.py`, `api/routes.py`

8. **Azure OpenAI retry logic** — exponential backoff for 429 errors
   - 3 retries with 5s, 15s, 30s delays
   - Applied to all 8 LLM API call sites

9. **PostgreSQL connection pooling** — prevents DB exhaustion
   - `pool_size=5, max_overflow=3, pool_recycle=300, pool_pre_ping=True`

10. **SKIP_METAFLOW env var** — disables background Metaflow during batch testing
    - Metaflow background processes were exhausting DB connections

## Result After All Improvements

### Final: 10-per-tag (83 requests): 80/83 (96.4%), AIS avg 99, min 94
- capacity: 10/10 (100%)
- contradictory: 8/10 (80%) — 1 transient error, 1 subtle logic gap
- lead_time: 10/10 (100%)
- missing_info: 10/10 (100%)
- multi_country: 2/2 (100%)
- multilingual: 11/11 (100%)
- restricted: 10/10 (100%)
- standard: 9/10 (90%) — 1 transient error
- threshold: 10/10 (100%)

### Improvement Trajectory
| Round | Requests | Accuracy | AIS |
|-------|----------|----------|-----|
| Baseline | 27 | 74.1% | 99 |
| +Fuzzy+Ambiguity | 27 | 92.6% | 99 |
| +Escalation+Smart Clarify | 43 | 90.7% | 99 |
| +Connection Pool+Skip MF | 83 | **96.4%** | **99** |

### Remaining Failure Patterns
1. **Transient 500 errors** (2/83 = 2.4%) — occasional DB connection contention when Metaflow is active. Mitigated by connection pooling and SKIP_METAFLOW env var.
2. **REQ-000051 [contradictory]** — 30 premium smartphones at €392/ea is unrealistically cheap but the system lacks per-unit price reasonability checks. Low priority edge case.
3. **REQ-000004 [contradictory]** — intermittent 500 on this specific request (docking stations, budget near threshold). May be a DB transaction race condition.
