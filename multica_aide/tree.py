"""Draft independent solutions, then improve the best measured solution."""
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import math
import os
from pathlib import Path
import re
import uuid


def select_parent(nodes, drafts=5, maximize=True, pending=(), eligible_ids=None):
    """Return a node to refine, or None for an independent draft.

    Nodes use ``score``, ``parent_id`` and ``kind`` (baseline/draft/refine).
    Pending round contexts count against the shared initial draft budget.
    """
    measured = [n for n in nodes if _has_score(n.get("score"))]
    completed = sum(n.get("kind") != "baseline" and not n.get("parent_id") for n in measured)
    reserved = sum(c["mode"] == "draft" for c in pending)
    if completed + reserved < drafts:
        return None
    eligible = set(eligible_ids) if eligible_ids is not None else None
    candidates = [n for n in measured if eligible is None or n['id'] in eligible]
    return (max if maximize else min)(candidates, key=lambda n: n["score"], default=None)


def _has_score(score):
    return not isinstance(score, bool) and isinstance(score, (int, float)) and math.isfinite(score)


def _score(score):
    if not _has_score(score):
        raise ValueError("a solution requires a finite numeric score")
    return score


def _identity(identity):
    if not isinstance(identity, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", identity):
        raise ValueError("node id must contain letters, digits, underscores, dots or hyphens")
    return identity


def _node(identity, title, description, score=None, **fields):
    if not isinstance(title, str) or not title.strip():
        raise ValueError("a solution requires a title")
    if not isinstance(description, str) or not description.strip():
        raise ValueError("a solution requires a description")
    return dict(id=_identity(identity), title=title, description=description, score=score, **fields)


class SearchTree:
    """Persist one shared journal; callers own artifacts and score computation."""

    def __init__(self, path, drafts=5, knowledge_base=None, maximize=True, score_function=None):
        if type(drafts) is not int or drafts < 0:
            raise ValueError("drafts must be a nonnegative integer")
        if not isinstance(maximize, bool):
            raise ValueError("maximize must be a boolean")
        self.path = Path(path).resolve()
        self.drafts = drafts
        self.knowledge_base = str(knowledge_base) if knowledge_base is not None else None
        self.maximize = maximize
        self.score_function = score_function or _score

    @contextmanager
    def _state(self):
        self.path.mkdir(parents=True, exist_ok=True)
        with (self.path / ".lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            source = self.path / "tree.json"
            state = json.loads(source.read_text()) if source.exists() else {
                "nodes": [], "baseline_id": None, "sessions": {}}
            yield state

    def _save(self, state):
        temporary = self.path / f".tree-{os.getpid()}.tmp"
        temporary.write_text(json.dumps(state, indent=2, allow_nan=False) + "\n")
        temporary.replace(self.path / "tree.json")

    def snapshot(self):
        """Return the journal and pending client contexts as JSON-compatible data."""
        with self._state() as state:
            return state

    def register_baseline(self, id, title, description, score, artifact=None, review=None):
        node = _node(id, title, description, _score(self.score_function(score)), delivery=score, artifact=artifact, review=review,
                     parent_id=None, kind="baseline", session_id=None, round_id=None)
        with self._state() as state:
            existing = next((n for n in state["nodes"] if n["id"] == id), None)
            if state["baseline_id"] is not None or existing:
                if existing and {k: existing[k] for k in node} == node and state["baseline_id"] == id:
                    return existing
                raise ValueError("the baseline is already registered; preserve its measured artifact")
            timestamp = datetime.now(timezone.utc).isoformat()
            node.update(created_at=timestamp, put_forward_at=timestamp, scored_at=timestamp,
                        test_duration_seconds=0.0)
            state["nodes"].append(node)
            state["baseline_id"] = id
            self._save(state)
        return node

    def register_baseline_description(self, id, title, description, *, session_id, artifact=None):
        """Register the untested root before its first measurement."""
        node = _node(id, title, description, artifact=artifact, review=None, parent_id=None,
                     kind='baseline', session_id=session_id, round_id=None)
        with self._state() as state:
            if state['baseline_id'] is not None:
                existing = next(n for n in state['nodes'] if n['id'] == state['baseline_id'])
                if existing['id'] == id and existing['session_id'] == session_id:
                    return existing
                raise ValueError('another baseline is already registered')
            timestamp = datetime.now(timezone.utc).isoformat()
            node.update(created_at=timestamp, put_forward_at=timestamp, scored_at=None, test_duration_seconds=None)
            state['nodes'].append(node)
            state['baseline_id'] = id
            self._save(state)
        return node

    def next_context(self, session_id, *, eligible_ids=None):
        if not isinstance(session_id, str) or not session_id.strip():
            raise ValueError("a client session_id is required")
        with self._state() as state:
            if session_id in state["sessions"]:
                return state["sessions"][session_id]
            if state["baseline_id"] is None:
                raise ValueError("register a scored baseline before starting clients")
            selected = select_parent(state['nodes'], self.drafts, self.maximize, state["sessions"].values(), eligible_ids)
            baseline = next(n for n in state["nodes"] if n["id"] == state["baseline_id"])
            if baseline['score'] is None:
                raise ValueError('score the baseline before starting improvement rounds')
            context = {"session_id": session_id, "round_id": uuid.uuid4().hex,
                       "mode": "refine" if selected else "draft", "parent_id": selected["id"] if selected else None,
                       "restore_node": selected or baseline,
                       "tree_summary": [{k: node.get(k) for k in ("id", "title", "description", "review", "score", "parent_id", "kind",
                                                                "delivery", "put_forward_at", "scored_at", "test_duration_seconds")}
                                        for node in state["nodes"]],
                       "knowledge_base": self.knowledge_base}
            state["sessions"][session_id] = context
            self._save(state)
            return context

    def cancel_round(self, session_id, round_id=None):
        """Release an unscored round after its owner handles the failure."""
        with self._state() as state:
            pending = state["sessions"].get(session_id)
            if pending is None:
                return None
            if round_id is not None and pending["round_id"] != round_id:
                raise ValueError("cancellation does not match the client's pending round")
            del state["sessions"][session_id]
            self._save(state)
            return pending

    def rescore(self, id, score, review=None):
        """Update a reevaluated node; its caller retains the measurement evidence."""
        delivery, score = score, _score(self.score_function(score))
        with self._state() as state:
            node = next((n for n in state['nodes'] if n['id'] == id), None)
            if node is None:
                raise ValueError('the reevaluated node is not registered')
            if node['score'] is None:
                raise ValueError('register the initial score before reevaluation')
            node['score'] = score
            node['delivery'] = delivery
            if review is not None:
                node['review'] = review
            self._save(state)
            return node

    def _description(self, state, id, title, description, session_id, round_id, parent_id, artifact, metadata,
                     put_forward_at=None):
        if not isinstance(session_id, str) or not session_id.strip():
            raise ValueError("a client session_id is required")
        if state["baseline_id"] is None:
            raise ValueError("register a scored baseline first")
        existing = next((n for n in state["nodes"] if n["id"] == id), None)
        pending = state["sessions"].get(session_id)
        context = pending or existing or {}
        if pending and ((round_id is not None and round_id != pending["round_id"])
                        or (parent_id is not None and parent_id != pending["parent_id"])):
            raise ValueError("submission does not match the client's pending round")
        if parent_id is None:
            parent_id = context.get("parent_id")
        if parent_id is not None and not any(n["id"] == parent_id and _has_score(n.get("score")) for n in state["nodes"]):
            raise ValueError("the parent node is not registered with a score")
        node = _node(id, title, description, parent_id=parent_id,
                     kind=pending["mode"] if pending else "refine" if parent_id else "draft",
                     session_id=session_id, round_id=round_id or context.get("round_id"),
                     artifact=artifact, metadata=metadata)
        if existing:
            if all(existing.get(k) == value for k, value in node.items() if k != "score"):
                return existing
            raise ValueError("node id is already registered with different content")
        if put_forward_at is None:
            timestamp = datetime.now(timezone.utc).isoformat()
        else:
            submitted = datetime.fromisoformat(put_forward_at)
            if submitted.tzinfo is None:
                raise ValueError('put_forward_at must include its UTC offset')
            timestamp = submitted.astimezone(timezone.utc).isoformat()
        node.update(review=None, created_at=timestamp, put_forward_at=timestamp,
                    scored_at=None, test_duration_seconds=None)
        state["nodes"].append(node)
        return node

    def register_description(self, id, title, description, *, session_id, round_id=None, parent_id=None,
                             artifact=None, metadata=None, put_forward_at=None):
        """Publish a proposal while retaining its pending client round for scoring."""
        with self._state() as state:
            node = self._description(state, id, title, description, session_id, round_id, parent_id, artifact, metadata,
                                     put_forward_at)
            self._save(state)
            return node

    def _finish_score(self, state, node, score, review):
        delivery, score = score, _score(self.score_function(score))
        if node["score"] is not None:
            if node["score"] == score and node["review"] == review and node.get('delivery', score) == delivery:
                return node
            raise ValueError("node score is already registered with different content; use rescore for reevaluation")
        scored_at = datetime.now(timezone.utc)
        node.update(score=score, delivery=delivery, review=review, scored_at=scored_at.isoformat(),
                    test_duration_seconds=(scored_at - datetime.fromisoformat(node["put_forward_at"])).total_seconds())
        pending = state["sessions"].get(node["session_id"])
        if pending and pending["round_id"] == node["round_id"]:
            del state["sessions"][node["session_id"]]
        return node

    def register_score(self, id, score, review=None):
        """Record a proposal's measured result and complete its matching round."""
        _score(self.score_function(score))
        with self._state() as state:
            node = next((n for n in state["nodes"] if n["id"] == id), None)
            if node is None:
                raise ValueError("register the solution description before its score")
            self._finish_score(state, node, score, review)
            self._save(state)
            return node

    def register(self, id, title, description, score, *, session_id, round_id=None, parent_id=None,
                 artifact=None, review=None, metadata=None):
        """Publish a measured node in one call, completing its pending round."""
        _score(self.score_function(score))
        with self._state() as state:
            node = self._description(state, id, title, description, session_id, round_id, parent_id, artifact, metadata)
            self._finish_score(state, node, score, review)
            self._save(state)
            return node
