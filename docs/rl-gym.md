# Agent gym (`patchquest.rl`)

Evaluation-corpus tasks as Gymnasium-shaped episodes, plus trajectory recording for agent research. The production
runtime is not involved; the gym reuses the shadow workspace, the verified edit engine, the command policy, the safe
executor, SecretGuard and the evaluation package's oracle path. There is **no training loop** here.

## Episode contract

```python
from patchquest.rl import PatchQuestEnv, OraclePolicy, rollout
env = PatchQuestEnv()                         # bundled corpus; PatchQuestEnv(tasks=[...]) for your own
obs, info = env.reset(seed=0, task_id=None)   # seed picks tasks[seed % n] (sorted by id) unless task_id is given
obs, reward, terminated, truncated, info = env.step({"type": "read_file", "path": "m.py"})
env.close()
```

* An episode is one task materialised from its *visible* files into a fresh `ShadowWorkspace` (temp dir).
  `reset` is a pure function of (task, seed): same inputs give a byte-identical workspace and first observation.
* `terminated` after a `finish` action; `truncated` when `max_steps` is used up. The hidden oracle runs only at
  those two moments. `step` after the end raises `EpisodeError`; every other bad input becomes an observation.
* `EnvConfig`: `max_steps` (30), `max_command_seconds` (30, kills the process group), `max_output_bytes` (8000,
  caps both captured command output and every observation text).
* Observation: `task_id`, `instruction`, `file_tree` (visible files; credential files, `.git`, ignored dirs and
  symlinks are omitted), `last_action` (`type`, `ok`, `output`, `truncated`; secret-redacted, host paths replaced by
  `<workspace>`, timings normalised) and `steps_left`.
* `info` carries `reward_breakdown` (a `RewardBreakdown`), cumulative `counters`, and at episode end
  `oracle_passed`, `forbidden_change`, `success` (= oracle passed and no forbidden change) and `changed_lines`.

### Actions

Plain dicts, validated strictly (exact fields, string values, size caps) by `patchquest.rl.actions.parse_action`.

| type | fields | notes |
| --- | --- | --- |
| `read_file` | `path` | text only, capped |
| `list_dir` | `path` | `"."` for the root |
| `search` | `query` | literal, case-sensitive, 50 hits max |
| `edit` | `path`, `search`, `replace` | verified edit engine: unique match required |
| `create_file` | `path`, `content` | refuses to overwrite |
| `run_tests` | none | the task's visible test command via the safe executor, after the command policy |
| `finish` | none | ends the episode |

Malformed or unknown actions return an `invalid` observation with a small negative reward. A well-formed action that
fails to apply (ambiguous edit, missing file, unsafe path) returns `ok: false` and is not "invalid".

## Reward

`reward = RewardBreakdown.total`, the sum of the components below (weights in the frozen `RewardConfig`; exact
semantics are in the `patchquest/rl/reward.py` docstring).

| component | default | when |
| --- | --- | --- |
| `oracle` | +1.0 | episode end, hidden oracle passes |
| `test_fraction` | 0.2 x delta | each `run_tests`: change in visible pass fraction vs the previous run (baseline = pristine workspace) |
| `patch_size` | -0.002/line, cap -0.2 | episode end, added+removed lines of the final diff |
| `step_cost` | -0.01 | every step |
| `safety` | -0.5 each | policy-blocked `run_tests` command; once at the end if a `forbid_changes` file changed |
| `invalid` | -0.05 | malformed/unknown action |

## Trajectories

`rollout(env, policy, seed)` returns a `Trajectory`; `traj.dump(path)` writes JSONL (`schema_version` 1):

* line 1 `{"kind": "header", "schema_version", "task_id", "seed", "fingerprint", "outcome"}`; the fingerprint holds
  a digest of the task set (a hash only, never oracle content), Python version, env and reward config.
* then one `{"kind": "step", "index", "observation", "action", "result", "reward", "terminated", "truncated",
  "wall_s", "counters"}` per step. `observation` is what the agent saw before acting.

Exports are always secret-redacted; `drop_file_contents=True` also removes file text (read/search/edit/create
payloads; `run_tests` output stays). `load_trajectory` refuses a file with a newer `schema_version`.

## Dataset kinds

`export_dataset(trajectories, kind)` is independent of input order:

* `successful` / `failed`: one record per trajectory (`observation`/`action` per step), sorted by task, seed, id.
* `paired`: per task with at least one success and one failure, `{"task_id", "chosen", "rejected"}`. Chosen is the best
  success (highest reward, then fewest steps, seed, id); rejected is the best failure by the same key.

## Safety properties

* Oracle isolation: the workspace never contains oracle files; they exist only in memory and are written into a
  throwaway copy at episode end, overwriting any same-named file the agent planted. Tests cover traversal, symlinks,
  guessing the oracle file name and `search`.
* All paths go through `resolve_in_repo` (no absolute, `..`, symlink escape, `.git` writes, credential dirs).
* Commands: only the task's own test command, after `classify`; scrubbed environment, timeout, output cap.
* `RandomPolicy` fuzzes the boundary with hostile paths; tests assert nothing is written outside the workspace.

Not covered: `run_tests` executes agent-edited code, which can do anything the OS user can (no network or filesystem
sandbox), and an agent can reward-hack the oracle by shadowing the test runner (for example creating `unittest.py`).
Run untrusted agents in a container.

## Using real Gymnasium

`GymAdapter` has the same 5-tuple shape with plain-dict `observation_space`/`action_space` (`ACTION_SCHEMAS` lists each
action's fields). To wrap:

```python
import gymnasium as gym
from patchquest.rl import GymAdapter, PatchQuestEnv

class PatchQuestGym(gym.Env):
    def __init__(self):
        self._inner = GymAdapter(PatchQuestEnv())
        self.observation_space = gym.spaces.Dict({...})   # build from GymAdapter.observation_space
        self.action_space = gym.spaces.Dict({...})        # build from ACTION_SCHEMAS
    def reset(self, *, seed=None, options=None):
        super().reset(seed=seed)
        return self._inner.reset(seed=seed, options=options)
    def step(self, action):
        return self._inner.step(action)
    def close(self):
        self._inner.close()
```

## CLI

`python -m patchquest.rl.rollout --task bugfix-leap-year --policy oracle --out run.jsonl` (`--policy random --seed N`,
`--max-steps`, `--drop-file-contents`).
