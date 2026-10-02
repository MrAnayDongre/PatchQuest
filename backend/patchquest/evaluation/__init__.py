"""Evaluation platform: measure PatchQuest itself against a versioned corpus with hidden oracles."""

from patchquest.evaluation.metrics import TaskResult, compare, summarize
from patchquest.evaluation.runner import run_eval
from patchquest.evaluation.tasks import EvalTask, corpus_digest, load_corpus

__all__ = ["EvalTask", "TaskResult", "compare", "corpus_digest", "load_corpus", "run_eval", "summarize"]
