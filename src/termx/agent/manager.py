from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import mimetypes
import uuid
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from time import monotonic, time
from typing import Any, TypeVar

from termx.agent.computer import ComputerController
from termx.agent.context import ContextEngine
from termx.agent.context.engine import (
    checkpoint_message,
    checkpoint_message_payload,
    estimate_tokens,
)
from termx.agent.metrics import TaskMetrics
from termx.agent.observation import (
    ObservationTracker,
    crop_region,
    jpeg_size,
    scale_for_model,
)
from termx.agent.retry import classify, retrying
from termx.agent.policy import (
    PolicyDecision,
    evaluate_computer,
    is_sensitive_path,
    redact,
)
from termx.agent.providers import (
    OpenAIResponsesAdapter,
    ProviderAdapter,
    ProviderCall,
    ProviderError,
    ProviderTurn,
)
from termx.agent.recovery import public_checkpoint, recovery_decision
from termx.agent.runtime import ProviderHttpRuntime
from termx.agent.scheduler import CallScheduler
from termx.agent.secrets import CredentialStore
from termx.agent.tree_budget import TreeTimeExceeded
from termx.agent.store import ACTIVE_STATUSES, AgentStore, configured_models
from termx.agent.limits import DEFAULT_LIMITS, resolve_limits
from termx.agent.tools import ToolContext, ToolOutcome, default_registry
from termx.agent import worktrees

T = TypeVar("T")
AdapterFactory = Callable[[dict[str, Any], str], ProviderAdapter]


class AgentManager:
    """Coordinates durable Agent runs, approvals, and live event replay."""

    def __init__(
        self,
        store: AgentStore,
        credentials: CredentialStore,
        desktop: Any,
        *,
        adapter_factory: AdapterFactory | None = None,
        project_files: Any = None,
        runner_for: Any = None,
        settings: Callable[[], Any] | None = None,
    ) -> None:
        self.store = store
        self._settings = settings
        self.credentials = credentials
        self.desktop = desktop
        self._adapter_factory = adapter_factory or self._default_adapter
        self._workers: dict[str, asyncio.Task[None]] = {}
        self._cancel: dict[str, asyncio.Event] = {}
        self._subscribers: dict[str, set[asyncio.Queue[dict[str, Any]]]] = defaultdict(set)
        # Global event listeners (notification center, etc.) — fired for every
        # persisted event in addition to the per-task subscriber queues.
        self._listeners: set[Callable[[str, dict[str, Any]], None]] = set()
        self._steering: dict[str, list[str]] = defaultdict(list)
        self._pending_approval_calls: dict[str, dict[str, Any]] = {}
        self._computer = ComputerController()
        self._tools = default_registry()
        self.browser = None  # AppState attaches the managed broker after mounting.
        self._scheduler = CallScheduler(self._tools)
        self._http = ProviderHttpRuntime()
        self._metrics: dict[str, TaskMetrics] = {}
        self._context_engines: dict[str, ContextEngine] = {}
        self._subagents: dict[str, dict[str, _SubagentHandle]] = {}
        self._observation = ObservationTracker()
        from termx.agent.policies.engine import PolicyEngine
        from termx.sandbox import runner_for as default_runner_for

        from termx.agent.tree_budget import TreeBudget
        self.tree_budget = TreeBudget(self.store)
        self._sandbox_runners: dict[str, Any] = {}
        self._runner_for = runner_for or default_runner_for
        # One-shot capability grants recorded when a capability approval is
        # approved without a remember scope — keyed (task_id, call_id) so the
        # approved spawn can realize the grant without a durable rule.
        self._one_shot_capability_grants: dict[tuple[str, str], set[str]] = {}
        self._policy_engine = PolicyEngine(
            self.store,
            binding_lookup=self._coding_policy_binding,
            envelope=lambda profile: self._sandbox_runner(profile).capabilities().granted,
            grantable=lambda profile: self._sandbox_runner(profile).capabilities().grantable,
        )
        from termx.runbooks import RunbookRunner

        self.runbooks = RunbookRunner(
            self.store,
            runner_for=self._sandbox_runner,
            policy_engine=self._policy_engine,
            project_id_for=self._project_id,
        )
        self._project_files = project_files
        for task in self.store.list_tasks(limit=500):
            # Native engines own their recovery. Budget approvals are durable
            # safe boundaries and must remain paused across host restarts.
            if task.get("engine") not in (None, "internal"):
                continue
            if (task.get("runtime") or {}).get("tree_budget_resume") and task["status"] in ACTIVE_STATUSES:
                pending_budget = any(a["kind"] == "budget" and a["status"] == "pending" for a in self.store.approvals(task["id"]))
                if not pending_budget:
                    runtime = task.get("runtime") or {}
                    self._pause_budget(task["id"], list(runtime.get("history") or []), int(runtime.get("step") or 0), monotonic(), "shared_resume")
                continue
            if task["status"] == "awaiting_approval" and any(
                a["kind"] == "budget" and a["status"] == "pending"
                for a in self.store.approvals(task["id"])
            ):
                continue
            if task["status"] in ACTIVE_STATUSES:
                checkpoint = self.store.latest_checkpoint(task["id"], kind="execution")
                decision = recovery_decision(checkpoint)
                if decision in {"resume_safe", "resume_committed", "resume_reuse"}:
                    self.store.update_task(task["id"], status="recovering")
                    self.store.append_event(
                        task["id"],
                        "task.recovery.started",
                        {
                            "checkpoint_id": checkpoint["id"],
                            "decision": decision,
                            "side_effect_state": checkpoint["side_effect_state"],
                        },
                    )
                elif decision == "confirm_required":
                    self.store.update_task(task["id"], status="recovery_confirmation_required")
                    self.store.append_event(
                        task["id"],
                        "task.recovery.started",
                        {
                            "checkpoint_id": checkpoint["id"] if checkpoint else None,
                            "decision": decision,
                            "side_effect_state": checkpoint["side_effect_state"] if checkpoint else None,
                        },
                    )
                    self.store.append_event(
                        task["id"],
                        "task.recovery.blocked",
                        {
                            "message": (
                                "A side-effecting tool call may have been in flight when the host "
                                "stopped. Confirming recovery may replay it."
                            ),
                            "checkpoint_id": checkpoint["id"] if checkpoint else None,
                        },
                    )
                else:
                    self.store.update_task(
                        task["id"],
                        status="failed",
                        error="The Termx host stopped before this task completed.",
                    )
                    self.store.append_event(
                        task["id"],
                        "task.failed",
                        {"message": "The Termx host stopped before this task completed."},
                    )

    # Provider configuration -----------------------------------------

    def list_providers(self) -> list[dict[str, Any]]:
        return self.store.list_providers()

    def save_provider(
        self,
        *,
        provider_id: str,
        kind: str,
        name: str,
        base_url: str,
        model: str,
        capabilities: list[str] | None,
        api_key: str | None = None,
    ) -> dict[str, Any]:
        current = self.store.get_provider(provider_id)
        if provider_id.startswith("chatgpt-") or (current and current["kind"] == "chatgpt"):
            raise ValueError("ChatGPT providers are managed through Sign in with ChatGPT")
        if kind not in {"openai", "openai-compatible"}:
            raise ValueError("provider kind must be openai or openai-compatible")
        if not base_url.startswith(("https://", "http://")):
            raise ValueError("provider URL must use HTTP or HTTPS")
        # These are account declarations, not a probe of model entitlement or
        # permission to invoke a billed operation. Preserve declarations on
        # credential rotation when the caller omits this optional field.
        declared = capabilities if capabilities is not None else (
            current.get("capabilities", ["shell"]) if current else ["shell"]
        )
        supported = {"shell", "functions", "computer", "image", "audio"}
        if not isinstance(declared, list) or len(declared) > len(supported) or any(
            not isinstance(item, str) or item not in supported for item in declared
        ):
            raise ValueError("provider capabilities must declare shell, functions, computer, image or audio")
        allowed = list(dict.fromkeys(declared)) or ["shell"]
        configured = bool(self.credentials.get(provider_id))
        if api_key:
            self.credentials.set(provider_id, api_key)
            configured = True
        # Config or key changed: drop the pooled client so the next adapter
        # does not reuse a stale base URL or credential.
        self._http.evict(provider_id)
        provider = self.store.put_provider(
            provider_id,
            kind=kind,
            name=name,
            base_url=base_url,
            model=model,
            capabilities=allowed,
            secret_configured=configured,
        )
        return provider

    async def test_provider(self, provider_id: str) -> dict[str, str]:
        provider = self._provider(provider_id)
        message = await self._adapter(provider).test()
        return {"status": "connected", "message": message}

    def delete_provider(self, provider_id: str) -> bool:
        current = self.store.get_provider(provider_id)
        if current and current["kind"] == "chatgpt":
            raise ValueError("Sign out of this ChatGPT account instead")
        deleted = self.store.delete_provider(provider_id)
        if deleted:
            self.credentials.delete(provider_id)
            self._http.evict(provider_id)
        return deleted

    # Task lifecycle --------------------------------------------------

    async def create_task(
        self,
        *,
        prompt: str,
        cwd: str,
        provider_id: str,
        limits: dict[str, Any] | None = None,
        mode: str = "agent",
        model: str | None = None,
        attachments: list[dict[str, Any]] | None = None,
        cancel: asyncio.Event | None = None,
        on_created: Callable[[str], None] | None = None,
        parent_id: str | None = None,
        execution_mode: str | None = None,
        conversation_id: str | None = None,
        custom_agent_id: str | None = None,
        custom_agent_snapshot: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        prompt = prompt.strip()
        if not prompt:
            raise ValueError("task prompt is required")
        custom_agent = (
            dict(custom_agent_snapshot) if custom_agent_snapshot else self.store.get_custom_agent(custom_agent_id) if custom_agent_id else None
        )
        if custom_agent_id and custom_agent is None:
            raise ValueError("custom agent not found")
        conversation = (
            self.store.get_conversation(conversation_id) if conversation_id else None
        )
        if conversation_id and conversation is None:
            raise ValueError("conversation not found")
        if custom_agent is None and conversation is not None:
            linked = conversation.get("custom_agent_id")
            if linked:
                custom_agent = self.store.get_custom_agent(str(linked))
        if custom_agent is not None:
            instructions = str(custom_agent.get("instructions") or "").strip()
            if instructions:
                prompt = (
                    f'You are the "{custom_agent["name"]}" agent. '
                    f"Follow these instructions:\n{instructions}\n\nTask: {prompt}"
                )
            if custom_agent.get("provider_id") and not provider_id:
                provider_id = str(custom_agent["provider_id"])
            if custom_agent.get("model") and not model:
                model = str(custom_agent["model"])
        if provider_id is None and conversation is not None and conversation.get("provider_id"):
            provider_id = str(conversation["provider_id"])
        if model is None and conversation is not None and conversation.get("model"):
            model = str(conversation["model"])
        agent_limits = dict(custom_agent.get("limits") or {}) if custom_agent else {}
        limits = {**agent_limits, **(limits or {})}
        mode = "ask" if mode == "ask" else "agent"
        execution = (execution_mode or "direct").strip().lower()
        if execution not in {"direct", "worktree"}:
            raise ValueError("execution_mode must be 'direct' or 'worktree'")
        root = Path(cwd).expanduser().resolve(strict=True)
        if not root.is_dir():
            raise ValueError("project folder is not a directory")
        worktree_spec: dict[str, str] | None = None
        if execution == "worktree":
            if mode == "ask":
                raise ValueError("Ask mode is read-only — worktrees require Agent mode")
            base_repo = worktrees.git_root(str(root))
            if base_repo is None:
                raise ValueError("worktree execution requires a Git project")
            slug = uuid.uuid4().hex[:12]
            worktree_spec = worktrees.create_worktree(base_repo, slug)
            worktree_spec["base_repo"] = base_repo
            # The worktree becomes the task's project boundary — the agent
            # cannot see or touch the user's working tree.
            root = Path(worktree_spec["worktree_path"])
        try:
            images = _decode_images(attachments)
            provider = dict(self._provider(provider_id))
            chosen = _resolve_model(provider, model)
            provider["model"] = chosen
            adapter = self._adapter(provider)
            bounded = self._limits(limits)
            task = self.store.create_task(
                prompt=prompt,
                cwd=str(root),
                provider_id=provider_id,
                model=chosen,
                limits=bounded,
                mode=mode,
                parent_id=parent_id,
                custom_agent_id=custom_agent["id"] if custom_agent is not None else None,
                custom_agent_snapshot=custom_agent,
            )
            task_id = task["id"]
            self.tree_budget.bind(task_id)
            if worktree_spec is not None:
                self.store.save_task_worktree(
                    task_id,
                    mode="worktree",
                    base_repo=worktree_spec["base_repo"],
                    base_ref=worktree_spec["base_ref"],
                    base_branch=worktree_spec.get("base_branch"),
                    worktree_path=worktree_spec["worktree_path"],
                    branch=worktree_spec["branch"],
                )
        except Exception:
            # Validation failed after the checkout was provisioned — remove it
            # so a rejected request never leaves an orphan worktree+branch.
            if worktree_spec is not None:
                try:
                    worktrees.discard_worktree(
                        worktree_spec["base_repo"],
                        worktree_spec["worktree_path"],
                        worktree_spec["branch"],
                    )
                except Exception:
                    pass
            raise
        if on_created is not None:
            on_created(task_id)
        uploads = [
            self.store.save_artifact(task_id, "upload", mime, data)
            for _name, mime, data in images
        ]
        if uploads:
            self._emit(task_id, "user.media", {"artifacts": uploads})
        self._emit(task_id, "task.created", {"task": task})
        if worktree_spec is not None:
            self._emit(
                task_id,
                "task.worktree.created",
                {
                    "worktree_path": worktree_spec["worktree_path"],
                    "branch": worktree_spec["branch"],
                    "base_repo": worktree_spec["base_repo"],
                    "base_ref": worktree_spec["base_ref"],
                    "base_branch": worktree_spec.get("base_branch"),
                },
            )
        seed = self._conversation_seed(conversation)
        upload_ids = [item["id"] for item in uploads]
        # Provider planning is execution work too. Persist its phase before
        # admission so an exhausted tree cannot bypass planning/plan approval.
        self.store.update_task(task_id, runtime={"tree_phase": "planning", "planning_seed": seed, "uploads": upload_ids})
        await self._meter_worker(task_id, self._prepare_task(task_id, cancel=cancel))
        ready = self._task(task_id)
        if ready["status"] == "planning" and ready.get("mode") == "ask":
            self._launch(task_id, self._drive(task_id))
        return self.store.get_task(task_id, include_events=True) or ready

    async def _prepare_task(self, task_id: str, *, cancel: asyncio.Event | None = None) -> None:
        task = self._task(task_id)
        runtime = task.get("runtime") or {}
        seed = list(runtime.get("planning_seed") or [])
        upload_ids = list(runtime.get("uploads") or [])
        cancel = self._cancel.setdefault(task_id, cancel or asyncio.Event())
        try:
            engine = ContextEngine(task["cwd"])
            manifest = await self._budget_await(task_id, _race_cancel(engine.snapshot(), cancel))
            self._context_engines[task_id] = engine
            self._metrics[task_id] = TaskMetrics()
            self._emit(task_id, "context.snapshot", {"snapshot": _snapshot_event(manifest)})
            history = seed + ([self._upload_message(task_id, upload_ids)] if upload_ids else [])
            if task.get("mode") == "ask":
                self.store.update_task(task_id, status="planning", runtime={"manifest": manifest, "history": history, "uploads": upload_ids, "step": 0, "started_at": None})
                return
            provider = dict(self._provider(task["provider_id"]))
            provider["model"] = str(task["model"])
            adapter = self._adapter(provider)
            plan_started = monotonic()
            plan, response_id = await self._budget_await(task_id, _race_cancel(adapter.plan(task["prompt"], task["cwd"], manifest), cancel))
            self._metrics[task_id].record_provider(int((monotonic() - plan_started) * 1000))
            self.store.update_task(task_id, status="awaiting_approval", plan=plan,
                previous_response_id=response_id,
                runtime={"manifest": manifest, "history": history, "uploads": upload_ids, "step": 0, "started_at": None})
            self._emit(task_id, "plan.ready", {"plan": plan})
            approval = self.store.create_approval(task_id, "plan", {"title": "Approve this plan", "plan": plan, "consequence": "Starts work on the paired host"})
            self._emit(task_id, "approval.requested", {"approval": approval})
            self._emit(task_id, "task.status", {"status": "awaiting_approval"})
        except TreeTimeExceeded:
            self._pause_budget(task_id, seed, 0, monotonic(), "shared_max_seconds")
        except asyncio.CancelledError:
            self._mark_cancelled(task_id)
        except Exception as exc:
            self._fail(task_id, exc)
        finally:
            self._cancel.pop(task_id, None)

    async def _resume_planning(self, task_id: str) -> None:
        await self._prepare_task(task_id)
        task = self._task(task_id)
        if task["status"] == "planning" and task.get("mode") == "ask":
            await self._drive(task_id)

    async def resolve_approval(
        self,
        task_id: str,
        approval_id: str,
        decision: str,
        *,
        remember: str | None = None,
        limits: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        task = self._task(task_id)
        approval = self.store.get_approval(approval_id)
        if approval is None or approval["task_id"] != task_id:
            raise KeyError(approval_id)
        # Budget wrappers are durable parent-side decisions. Reconstruct only
        # their verified child binding after restart, never an arbitrary tool
        # request from its public payload.
        wrapper = self._pending_approval_calls.get(approval_id)
        if approval['kind'] == 'budget' and not wrapper:
            payload = approval.get('payload') or {}
            child = self.store.get_task(str(payload.get('child_id') or ''))
            inner = self.store.get_approval(str(payload.get('child_approval_id') or ''))
            if child and inner and child.get('parent_id') == task_id and inner['task_id'] == child['id'] and inner['kind'] == 'budget':
                wrapper = {'task_id':task_id,'child_id':child['id'],'child_approval_id':inner['id']}
                self._pending_approval_calls[approval_id] = wrapper
        escalation = "child_approval_id" in (self._pending_approval_calls.get(approval_id) or {})
        if task["status"] != "awaiting_approval" and not (
            escalation and approval["status"] == "pending"
        ):
            raise ValueError("task is not awaiting approval")
        if approval["status"] != "pending":
            raise ValueError("approval is already resolved")
        if approval['kind'] == 'budget' and escalation:
            # Validate and commit the real child grant before consuming its UI
            # wrapper. A stale/invalid renewal must stay inspectable on both
            # tasks. The child alone owns the atomic grant/version receipt.
            child_id = str(wrapper['child_id'])
            child = self._task(child_id)
            inner = self.store.get_approval(str(wrapper['child_approval_id']))
            if child.get('parent_id') != task_id or not inner or inner['task_id'] != child_id or inner['kind'] != 'budget':
                raise ValueError('Child budget request no longer belongs to this parent')
            if inner['status'] == 'pending':
                await self.resolve_approval(child_id,inner['id'],decision,limits=limits)
            elif inner['status'] != decision:
                raise ValueError('Child budget decision changed; inspect the current task')
            approval = self.store.resolve_approval(approval_id,decision)
            self._pending_approval_calls.pop(approval_id,None)
            self._emit(task_id,'approval.resolved',{'approval':approval})
            self._unpause_if_idle(task_id)
            return approval
        browser_pending=self._pending_approval_calls.get(approval_id) or {}
        if browser_pending.get('browser_review_id') and decision!='denied' and self.browser:
            reviewed=self.browser.records.get('review',browser_pending['browser_review_id'])
            if not reviewed or reviewed['status']!='needs_user' or reviewed['expires_at']<=time():
                raise ValueError('Browser page, control or approval expired; take over and request a fresh observation')
        next_limits = None
        renew_shared = True
        if approval["kind"] == "budget" and decision == "approved":
            step = int((task.get("runtime") or {}).get("step") or 0)
            suggested = approval["payload"]["suggested_limits"]
            shared=approval['payload'].get('tree_budget')
            if shared:
                current=self.tree_budget.snapshot(task_id)
                if approval['payload'].get('reuse_grant') and not current['reason']:
                    renew_shared=False
                    suggested={'max_steps':current['max_steps'],'max_seconds':current['max_execution_seconds']}
                elif current['version']!=shared['version']:
                    # Another task already received the explicit tree grant.
                    # This decision resumes within that existing allowance; it
                    # cannot extend it or reset the shared time meter again.
                    if current['reason']:raise ValueError('The shared allowance is exhausted again; inspect the newest tree budget request')
                    renew_shared=False
                    suggested={'max_steps':current['max_steps'],'max_seconds':current['max_execution_seconds']}
            next_limits = resolve_limits(limits if renew_shared else None, {**task["limits"], **suggested})
            if shared and next_limits['max_steps']<=current['used_steps']:
                raise ValueError('The total tree step ceiling must exceed the already reserved calls')
            if next_limits["max_steps"] <= step:
                raise ValueError("max_steps must exceed the completed tool count")
        if (
            decision != "denied"
            and approval["kind"] == "tool"
            and approval_id not in self._pending_approval_calls
        ):
            raise ValueError("approved tool request is no longer available")
        if remember and remember != 'once':
            if remember not in (approval.get('payload') or {}).get('remember_options',[]):
                raise ValueError('This approval does not permit the requested remembered scope')
            intent_record=(self._pending_approval_calls.get(approval_id) or {}).get('intent') or {}
            expected=intent_record.get('consent_binding') or {}
            current=self._coding_policy_binding(task_id)
            if expected and expected != current or current.get('unbound'):
                raise PermissionError('Task authority changed; request fresh consent')
        if approval["kind"] == "budget" and decision == "approved" and approval['payload'].get('tree_budget') and not escalation:
            approval = self.tree_budget.approve(task_id, approval_id, next_limits, version=current['version'] if not renew_shared else shared['version'], renew=renew_shared)
        else:
            approval = self.store.resolve_approval(approval_id, decision)
        metrics = self._metrics.get(task_id)
        if metrics is not None and approval.get("resolved_at") and approval.get("created_at"):
            metrics.record_approval_wait(int((approval["resolved_at"] - approval["created_at"]) * 1000))
        self._emit(task_id, "approval.resolved", {"approval": approval})
        private_payload = self._pending_approval_calls.pop(approval_id, None)
        if private_payload and private_payload.get('browser_review_id') and self.browser:
            binding=self.browser.records.get('agent-task-authority',task_id) or self.browser.records.get('browser-task',task_id)
            if binding:
                reviewed=self.browser.records.get('review',private_payload['browser_review_id'])
                # Takeover/revocation already invalidates the browser permit.
                # A denial must still settle the agent's parked approval.
                if reviewed and reviewed['status']=='needs_user':
                    self.browser.review.decide(private_payload['browser_review_id'],principal_id=binding['principal_id'],approve=decision!='denied')
        if private_payload is not None and "child_approval_id" in private_payload:
            # A sub-agent's consequential action was escalated to this parent;
            # relay the decision AND the remember scope — the durable rule is
            # written with the child's context so it can't outlive its task.
            child_id = str(private_payload["child_id"])
            try:
                await self.resolve_approval(
                    child_id,
                    str(private_payload["child_approval_id"]),
                    decision,
                    remember=remember,
                    limits=limits or next_limits,
                )
            except (KeyError, ValueError):
                pass
            # Async sub-agents let the parent finish while an escalation is
            # open; only wake it back to running when genuinely idle.
            self._unpause_if_idle(task_id)
            return approval
        if remember:
            self._record_remembered_rule(
                task,
                approval,
                private_payload,
                decision=decision,
                remember=remember,
            )
        if decision == "denied":
            self._finish_state(task_id)
            self.store.update_task(task_id, status="cancelled", error="Approval denied")
            self._emit(task_id, "task.cancelled", {"message": "Approval denied"})
            return approval
        if approval["kind"] == "budget":
            if next_limits is None:
                self._mark_cancelled(task_id)
                return approval
            runtime = dict(task.get("runtime") or {})
            runtime["started_at"] = None
            runtime.pop("tree_budget_resume", None)
            self.store.update_task(task_id, limits=next_limits, runtime=runtime)
            self._emit(task_id, "task.budget.extended", {"limits": next_limits})
            self._launch(task_id, self._resume_planning(task_id) if runtime.get("tree_phase") == "planning" else self._drive(task_id))
        elif approval["kind"] == "plan":
            self._launch(task_id, self._drive(task_id))
        elif approval["kind"] == "tool":
            assert private_payload is not None
            if private_payload.get("approval_kind") == "capability" and not remember:
                # One-shot capability grant: the approved spawn may realize
                # the capability for this call only — no durable rule exists.
                call_id = str((private_payload.get("call") or {}).get("call_id") or "")
                profile = str(private_payload.get("sandbox_profile") or "agent")
                caps = {
                    c
                    for c in private_payload.get("required_capabilities") or []
                    if self._policy_engine.grantable_covers(profile, str(c))
                }
                if call_id and caps:
                    self._one_shot_capability_grants[(task_id, call_id)] = caps
            self._launch(task_id, self._resume_approved(task_id, private_payload))
        return approval

    def _record_remembered_rule(
        self,
        task: dict[str, Any],
        approval: dict[str, Any],
        private_payload: dict[str, Any] | None,
        *,
        decision: str,
        remember: str,
    ) -> None:
        """Persist the durable rule implied by a `remember=...` resolution.

        Emits ``policy.created`` on success. Failure never un-resolves the
        approval — it emits ``policy.rejected`` with the reason instead.
        """
        from termx.agent.policies.models import PolicyIntent, rule_public

        intent_record = (private_payload or {}).get("intent")
        if not intent_record:
            self._emit(
                task["id"],
                "policy.rejected",
                {"approval_id": approval["id"], "reason": "no policy intent recorded"},
            )
            return
        intent = PolicyIntent.from_record(intent_record)
        approval_kind = str((private_payload or {}).get("approval_kind") or "action")
        if approval_kind == "capability":
            # The intent carries the tool call's full capability requirement;
            # the approval only elevated the decision's still-missing set,
            # stored on the payload. Persist just those — the baseline caps
            # aren't grantable and would be rejected by rule validation.
            asked = (private_payload or {}).get("required_capabilities") or []
            intent = replace(
                intent, required_capabilities=tuple(str(c) for c in asked)
            )
        try:
            rule = self._policy_engine.record_resolution(
                intent,
                decision=decision,
                remember=remember,
                project_id=self._project_id(task["cwd"]),
                source_approval_id=approval["id"],
                approval_kind=approval_kind,
            )
        except (ValueError, KeyError) as exc:
            self._emit(
                task["id"],
                "policy.rejected",
                {"approval_id": approval["id"], "reason": str(exc)},
            )
            return
        if rule is not None:
            self._emit(
                task["id"],
                "policy.created",
                {"rule": rule_public(rule), "approval_id": approval["id"]},
            )

    def cancel(self, task_id: str) -> dict[str, Any]:
        task = self._task(task_id)
        if task["status"] not in ACTIVE_STATUSES:
            return task
        cancel = self._cancel.get(task_id)
        if cancel is not None:
            cancel.set()
            self.store.update_task(task_id, status="cancelling")
            self._emit(task_id, "task.status", {"status": "cancelling"})
        else:
            # No live worker (e.g. paused on approval): finalize in-place.
            self._finish_state(task_id)
            self.store.update_task(task_id, status="cancelled", error="Cancelled by user")
            self._emit(task_id, "task.cancelled", {"message": "Cancelled by user"})
        return self._task(task_id)

    async def takeover(self, task_id: str) -> dict[str, Any]:
        task = self._task(task_id)
        if task["status"] not in ACTIVE_STATUSES:
            return task
        self.cancel(task_id)
        await self._computer.release_all()
        if self._observation.control_owner != "user":
            self._observation.control_owner = "user"
            self._emit(task_id, "computer.control.changed", {"owner": "user"})
        if not any(event["type"] == "control.takeover" for event in self.store.events(task_id)):
            self._emit(task_id, "control.takeover", {"message": "You have control"})
        self._mark_cancelled(task_id)
        return self._task(task_id)

    def _cleanup_orphan_worktree(self, record: dict[str, Any] | None) -> None:
        """Best-effort removal of a worktree+branch whose task row is gone.
        'active' records only — 'kept' worktrees belong to the user."""
        if not record or record.get("status") != "active":
            return
        path = record.get("worktree_path")
        base = record.get("base_repo")
        if not path or not base:
            return
        try:
            worktrees.discard_worktree(base, str(path), str(record.get("branch") or ""))
        except Exception:
            pass

    def delete_task(self, task_id: str) -> bool:
        record = self.store.task_worktree(task_id)
        deleted = self.store.delete_task(task_id)
        if deleted:
            self._cleanup_orphan_worktree(record)
        return deleted

    def prune_storage(
        self, *, retention_days: int | None = None, max_bytes: int | None = None
    ) -> list[str]:
        candidates = {
            task["id"]: self.store.task_worktree(task["id"])
            for task in self.store.list_tasks(limit=500)
            if task["status"] not in ACTIVE_STATUSES
        }
        removed = self.store.prune(retention_days=retention_days, max_bytes=max_bytes)
        for task_id in removed:
            self._cleanup_orphan_worktree(candidates.get(task_id))
        return removed

    def task_worktree(self, task_id: str) -> dict[str, Any]:
        self._task(task_id)
        record = self.store.task_worktree(task_id)
        if record is None:
            return {"task_id": task_id, "mode": "direct"}
        payload = dict(record)
        path = record["worktree_path"]
        if record["status"] == "active" and path and worktrees.worktree_exists(path):
            try:
                payload["dirty"] = worktrees.worktree_dirty(path)
            except Exception:
                payload["dirty"] = None
            try:
                payload["head_sha"] = worktrees.head_sha(path)
            except Exception:
                pass
            payload["diff"] = worktrees.diff_against(
                record["base_repo"], record["base_ref"], path
            )
        return payload

    def resolve_worktree(
        self, task_id: str, action: str, *, confirm: bool = False
    ) -> dict[str, Any]:
        task = self._task(task_id)
        record = self.store.task_worktree(task_id)
        if record is None or record["mode"] != "worktree":
            raise ValueError("task has no worktree")
        if record["status"] != "active":
            return record
        if task["status"] in ACTIVE_STATUSES:
            raise ValueError("worktree actions require a finished task")
        path = record["worktree_path"] or ""
        branch = record["branch"] or ""
        base = record["base_repo"]
        if action == "keep":
            updated = self.store.update_task_worktree(task_id, status="kept") or record
            self._emit(task_id, "task.worktree.kept", {"worktree": updated})
            return updated
        if action == "apply":
            try:
                head = worktrees.apply_worktree(
                    base, path, branch, task_id,
                    base_branch=record.get("base_branch"),
                )
            except worktrees.WorktreeApplyConflict as exc:
                self._emit(
                    task_id,
                    "task.worktree.conflict",
                    {"error": str(exc), "base_branch": record.get("base_branch")},
                )
                raise
            updated = self.store.update_task_worktree(
                task_id, status="applied", head_sha=head
            ) or record
            self._emit(task_id, "task.worktree.applied", {"worktree": updated})
            return updated
        if action == "discard":
            dirty = (
                worktrees.worktree_dirty(path) if worktrees.worktree_exists(path) else []
            )
            if dirty and not confirm:
                raise worktrees.WorktreeConfirmRequired(dirty)
            worktrees.discard_worktree(base, path, branch)
            updated = self.store.update_task_worktree(task_id, status="discarded") or record
            self._emit(task_id, "task.worktree.discarded", {"worktree": updated})
            return updated
        raise ValueError("worktree action must be apply, keep, or discard")

    def steer(self, task_id: str, message: str) -> dict[str, Any]:
        task = self._task(task_id)
        message = message.strip()
        if not message:
            raise ValueError("steering message is required")
        if task["status"] not in {"running", "awaiting_approval"}:
            raise ValueError("only an active task can be steered")
        self._steering[task_id].append(message)
        return self._emit(task_id, "task.steered", {"message": message})

    def subscribe(self, task_id: str) -> asyncio.Queue[dict[str, Any]]:
        self._task(task_id)
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=200)
        self._subscribers[task_id].add(queue)
        return queue

    def unsubscribe(self, task_id: str, queue: asyncio.Queue[dict[str, Any]]) -> None:
        subscribers = self._subscribers.get(task_id)
        if subscribers is not None:
            subscribers.discard(queue)
            if not subscribers:
                self._subscribers.pop(task_id, None)

    def emit_external(self, task_id: str, event_type: str,
                      payload: dict[str, Any]) -> dict[str, Any]:
        """Persist + broadcast an event produced outside the internal loop
        (native engine sessions route through here so WS subscribers work)."""
        return self._emit(task_id, event_type, payload)

    async def close(self) -> None:
        for event in self._cancel.values():
            event.set()
        watchers = [
            handle.watcher
            for handles in self._subagents.values()
            for handle in handles.values()
            if handle.watcher is not None and not handle.watcher.done()
        ]
        for watcher in watchers:
            watcher.cancel()
        workers = list(self._workers.values()) + watchers
        if workers:
            await asyncio.gather(*workers, return_exceptions=True)
        await self._computer.release_all()
        await self.runbooks.shutdown()
        await self._http.aclose()
        await asyncio.to_thread(self.store.prune)

    # Execution -------------------------------------------------------

    async def _drive(
        self,
        task_id: str,
        *,
        history: list[dict[str, Any]] | None = None,
        step: int | None = None,
        started_at: float | None = None,
    ) -> None:
        task = self._task(task_id)
        runtime = task.get("runtime") or {}
        history = list(history if history is not None else runtime.get("history") or [])
        step = int(step if step is not None else runtime.get("step") or 0)
        started_at = started_at or float(runtime.get("started_at") or monotonic())
        cancel = self._cancel.setdefault(task_id, asyncio.Event())
        engine = self._context_engines.setdefault(task_id, ContextEngine(task["cwd"]))
        metrics = self._metrics.setdefault(task_id, TaskMetrics(task.get("metrics")))
        self.store.update_task(task_id, status="running")
        self._emit(task_id, "task.status", {"status": "running"})
        try:
            while True:
                task = self._task(task_id)
                limits = task["limits"]
                if cancel.is_set():
                    await self._cancelled(task_id)
                    return
                shared = self.tree_budget.snapshot(task_id)
                reason = (shared['reason'] or ("max_steps" if step >= limits["max_steps"] else
                          "max_seconds" if monotonic() - started_at >= limits["max_seconds"] else None))
                if reason:
                    self._pause_budget(task_id, history, step, started_at, reason)
                    return
                parked=(task.get('runtime') or {}).get('tree_pending_calls')
                if parked:
                    paused,history,step=await self._run_calls(task_id,[self._call(call) for call in parked],history,step,started_at,
                        approved_first=bool((task.get('runtime') or {}).get('tree_approved_first')))
                    if paused:return
                    runtime={**(self._task(task_id).get('runtime') or {}),'history':history,'step':step}
                    runtime.pop('tree_pending_calls',None);runtime.pop('tree_approved_first',None)
                    self.store.update_task(task_id,runtime=runtime)
                steering = self._steering.pop(task_id, [])
                for message in steering:
                    history.append({"role": "user", "content": message})
                provider = dict(self._provider(task["provider_id"]))
                provider["model"] = str(task.get("model") or _resolve_model(provider, None))
                adapter = self._adapter(provider)
                manifest = await engine.snapshot()
                # Compaction runs BEFORE slim_history: the compactor must see
                # the un-trimmed transcript or older history would be dropped
                # permanently before it could be summarized durably.
                compaction = engine.plan_compaction(
                    history, git_state=manifest.get("git") if isinstance(manifest, dict) else None
                )
                if compaction is not None:
                    self._emit(
                        task_id,
                        "context.compaction.started",
                        {"estimated_tokens": estimate_tokens(history), "items": len(history)},
                    )
                    payload = dict(compaction["payload"])
                    payload["approvals"] = [
                        {"id": approval["id"], "kind": approval["kind"], "status": approval["status"]}
                        for approval in self.store.approvals(task_id)
                        if approval.get("status") == "pending"
                    ]
                    payload["artifacts"] = [
                        {"id": artifact["id"], "kind": artifact["kind"], "mime": artifact["mime"]}
                        for artifact in self.store.artifacts(task_id)
                    ][:20]
                    last_safe = self.store.latest_checkpoint(task_id, kind="execution")
                    payload["last_safe_execution_checkpoint"] = (
                        last_safe["id"] if last_safe else None
                    )
                    checkpoint = self.store.create_checkpoint(
                        task_id,
                        kind="context",
                        history_cursor=int(compaction["history_cursor"]),
                        plan_step=step,
                        payload=payload,
                    )
                    history = compaction["history"]
                    # Re-serialize the injected message with the real
                    # checkpoint id (its content stays a plain user message).
                    for item in history:
                        body = checkpoint_message_payload(item)
                        if body is not None:
                            item["content"] = checkpoint_message(
                                body["summary"], body["compacted_items"], checkpoint["id"]
                            )["content"]
                            break
                    self._emit(task_id, "context.checkpoint", {"checkpoint_id": checkpoint["id"]})
                    self._emit(
                        task_id,
                        "context.compaction.completed",
                        {
                            "checkpoint_id": checkpoint["id"],
                            "retained": len(history),
                            "dropped": compaction["dropped"],
                        },
                    )
                history = engine.slim_history(history)
                read_only = task.get("mode") == "ask"
                turn_started = monotonic()
                first_token: list[float] = []
                stream_id = uuid.uuid4().hex[:12]
                attempt_no = [0]

                def _on_delta(delta: str) -> None:
                    if not first_token:
                        first_token.append(monotonic())
                    self._emit(
                        task_id,
                        "assistant.delta",
                        {"text": delta, "stream_id": stream_id, "attempt": attempt_no[0]},
                    )

                def _on_stream_event(kind: str, data: dict[str, Any]) -> None:
                    self._emit(
                        task_id,
                        "provider.tool_call.delta",
                        {
                            "event": kind,
                            "stream_id": stream_id,
                            "attempt": attempt_no[0],
                            "call_id": str(data.get("call_id") or data.get("item_id") or ""),
                            "delta": str(data.get("delta") or "")[:200],
                        },
                    )

                stream_fn = (
                    getattr(adapter, "stream_turn", None)
                    if getattr(adapter, "supports_streaming", False)
                    else None
                )

                async def _request() -> Any:
                    attempt_no[0] += 1
                    kwargs: dict[str, Any] = {
                        "prompt": task["prompt"],
                        "cwd": task["cwd"],
                        "manifest": manifest,
                        "input_items": history if history else None,
                        "allow_computer": (not read_only) and "computer" in provider["capabilities"],
                        "read_only": read_only,
                        # Terminal-parent gate: children may not fan out further.
                        "allow_subagents": task.get("parent_id") is None,
                    }
                    if stream_fn is not None:
                        self._emit(
                            task_id,
                            "provider.stream.started",
                            {
                                "model": provider["model"],
                                "stream_id": stream_id,
                                "attempt": attempt_no[0],
                            },
                        )
                        try:
                            return await stream_fn(
                                **kwargs, on_delta=_on_delta, on_event=_on_stream_event
                            )
                        except asyncio.CancelledError:
                            raise
                        except Exception as exc:
                            # Abandoned stream attempts are recorded so the UI
                            # can discard partial text from this attempt.
                            self._emit(
                                task_id,
                                "provider.stream.aborted",
                                {
                                    "stream_id": stream_id,
                                    "attempt": attempt_no[0],
                                    "reason_class": classify(exc).kind,
                                },
                            )
                            raise
                    return await adapter.turn(**kwargs)

                def _emit_retry(event_type: str, payload: dict[str, Any]) -> None:
                    if event_type in {"provider.retry", "provider.rate_limited"}:
                        metrics.record_provider_retry(
                            rate_limited=event_type == "provider.rate_limited"
                        )
                    self._emit(task_id, event_type, payload)

                turn = await self._budget_await(task_id, _race_cancel(
                    retrying(
                        _request,
                        emit=_emit_retry,
                        cancel=cancel,
                        # Once a delta is visible, transparently restarting the
                        # stream would duplicate text in replay — fail instead.
                        may_retry=lambda: not first_token,
                    ),
                    cancel,
                ))
                stream_ms = int((monotonic() - turn_started) * 1000)
                metrics.record_provider(stream_ms, turn.usage)
                if stream_fn is not None:
                    first_ms = int((first_token[0] - turn_started) * 1000) if first_token else None
                    metrics.record_provider_stream(first_ms, stream_ms)
                    self._emit(
                        task_id,
                        "provider.stream.completed",
                        {
                            "response_id": turn.response_id,
                            "stream_id": stream_id,
                            "attempts": attempt_no[0],
                            "first_token_ms": first_ms,
                            "stream_ms": stream_ms,
                        },
                    )
                if cancel.is_set():
                    await self._cancelled(task_id)
                    return
                self.store.update_task(task_id, previous_response_id=turn.response_id)
                if turn.text:
                    self._emit(task_id, "assistant.message", {"text": turn.text})
                if turn.usage:
                    self._emit(task_id, "provider.usage", {"usage": turn.usage})
                history.extend(turn.output_items)
                if not turn.calls:
                    result = turn.text or "Task completed."
                    snapshot = metrics.snapshot()
                    self._finalize_worktree(task_id)
                    self.store.update_task(task_id, status="completed", result=result, runtime={}, metrics=snapshot)
                    self._emit(task_id, "task.metrics", {"metrics": snapshot})
                    self._emit(task_id, "task.completed", {"result": result})
                    return
                paused, history, step = await self._run_calls(
                    task_id,
                    turn.calls,
                    history,
                    step,
                    started_at,
                    turn_id=turn.response_id,
                )
                if paused:
                    return
                runtime = {"manifest": manifest, "history": history, "step": step, "started_at": started_at}
                self.store.update_task(task_id, runtime=runtime)
        except TreeTimeExceeded:
            self._pause_budget(task_id,history,step,started_at,'shared_max_seconds')
        except asyncio.CancelledError:
            await self._cancelled(task_id)
        except Exception as exc:
            if cancel.is_set():
                await self._cancelled(task_id)
            else:
                self._fail(task_id, exc)
        finally:
            self._cancel.pop(task_id, None)
            ended = self.store.get_task(task_id)
            if ended is not None and ended["status"] not in ACTIVE_STATUSES:
                self._context_engines.pop(task_id, None)
                self._metrics.pop(task_id, None)
                # Task-scoped remembered rules die with the task on EVERY
                # terminal transition — completed, failed, cancelled or a
                # crashed worker — not just the paths that call _finish_state.
                self.store.expire_task_policy_rules(task_id)

    def _pause_budget(self, task_id: str, history: list[dict[str, Any]],
                      step: int, started_at: float, reason: str) -> None:
        task = self._task(task_id)
        runtime = {**(task.get("runtime") or {}), "history": history,
                   "step": step, "started_at": started_at}
        self.store.update_task(task_id, status="awaiting_approval", runtime=runtime)
        chunk = self._limits(None)["max_steps"]
        shared=self.tree_budget.snapshot(task_id) if reason.startswith('shared_') else None
        suggested = {"max_steps": min(100000, max((shared['used_steps'] if shared else step) + chunk, (shared['max_steps'] if shared else task["limits"]["max_steps"])))}
        if shared:suggested['max_seconds']=shared['max_execution_seconds']
        approval = self.store.create_approval(task_id, "budget", {
            "title": "Continue the task tree with a shared execution budget" if shared else "Continue this task with a renewed run budget",
            **({'tree_budget':shared,'reuse_grant':reason=='shared_resume' and not shared['reason']} if shared else {}),
            "reason": reason, "completed_steps": step, "limits": task["limits"],
            "suggested_limits": suggested,
            "consequence": "Continues within the already approved shared allowance without resetting time or reserved calls" if reason == "shared_resume" and shared and not shared['reason'] else "Renews the shared sum of active parent/child execution seconds and the total tool-call ceiling; reserved calls and completed effects remain." if shared else "Continues from saved tool results; grants a fresh time budget",
        })
        self._emit(task_id, "task.budget.exhausted", approval["payload"])
        self._emit(task_id, "approval.requested", {"approval": approval})
        self._emit(task_id, "task.status", {"status": "awaiting_approval"})

    async def _run_calls(
        self,
        task_id: str,
        calls: list[ProviderCall],
        history: list[dict[str, Any]],
        step: int,
        started_at: float,
        *,
        approved_first: bool = False,
        turn_id: str | None = None,
    ) -> tuple[bool, list[dict[str, Any]], int]:
        task = self._task(task_id)
        read_only = task.get("mode") == "ask"
        ctx = self._tool_context(task_id, task)
        groups = self._scheduler.schedule(calls, ctx)
        index = 0
        for group in groups:
            preset=self.store.task_agent(task)
            if preset and preset.get('tools'):
                for entry in group:
                    name=entry.call.name or ('run_shell' if entry.call.type=='shell' else entry.call.type)
                    if name not in preset['tools']:raise PermissionError('This tool is outside the task agent preset')
            # A controlled browser grant must not be bypassed via arbitrary
            # shell, runbook, subagent, computer or Git process tools. Typed
            # project file/search operations remain available for code context.
            binding=self.browser.records.get('browser-task',task_id) if self.browser else None
            active_browser=bool(binding and binding.get('restricted') and any(
                grant['run_id']==task_id and not grant['revoked'] and grant['expires_at']>time()
                for grant in self.browser.records.list('grant')))
            if active_browser:
                broker_tools={'browser_tabs','browser_observe','browser_action','browser_open_tab','browser_close_tab','browser_wait_for_handoff','list_files','read_file','write_file','apply_patch','search_project','preview_list'}
                for item in group:
                    if item.call.type!='function' or item.call.name not in broker_tools:
                        raise PermissionError('Restricted browser task cannot run process/computer tools; take over all granted tabs before resuming coding execution')
            positions=[str(step+offset)+':'+hashlib.sha256(json.dumps(self._call_payload(entry.call),sort_keys=True).encode()).hexdigest() for offset,entry in enumerate(group)]
            shared_reason=self.tree_budget.reserve(task_id,positions)
            if shared_reason:
                current=self._task(task_id)
                runtime={**(current.get('runtime') or {}),'tree_pending_calls':[self._call_payload(call) for call in calls[index:]],'tree_approved_first':approved_first and index==0}
                self.store.update_task(task_id,runtime=runtime)
                self._pause_budget(task_id,history,step,started_at,shared_reason)
                return True,history,step
            if len(group) > 1:
                # A parallel-safe batch: registered read tools that never need
                # approval, run concurrently. Output order stays call order.
                self._emit(task_id, "tool.batch", {"calls": [entry.call.public() for entry in group]})
                for entry in group:
                    self._emit(task_id, "tool.started", {"call": entry.call.public()})
                results = await asyncio.gather(
                    *(self._invoke_tool(entry, ctx) for entry in group),
                    return_exceptions=True,
                )
                first_error: BaseException | None = None
                for entry, outcome in zip(group, results):
                    if isinstance(outcome, BaseException):
                        if first_error is None:
                            first_error = outcome
                        continue
                    self._emit(task_id, "tool.finished", outcome.finished_payload(entry.call))
                    history.extend(outcome.output_items(entry.call))
                    if outcome.cancelled and first_error is None:
                        first_error = asyncio.CancelledError()
                    step += 1
                    index += 1
                if first_error is not None:
                    raise first_error
                continue
            entry = group[0]
            call = entry.call
            if call.name in {'browser_tabs','browser_observe','browser_action','browser_open_tab','browser_close_tab'}:
                from termx.auto_review import ReviewRequired,ActionBlocked
                self._emit(task_id,'tool.started',{'call':call.public()})
                try:
                    outcome=await self._invoke_tool(entry,ctx)
                except ActionBlocked as exc:
                    outcome=ToolOutcome({'refused':True,'error':exc.record['reason'],'review_id':exc.record['id']})
                except ReviewRequired as exc:
                    public_payload={'title':'Browser action needs your approval','consequence':exc.record['reason'],'call':call.public(),'browser_review':exc.record,'remaining_calls':[item.public() for item in calls[index+1:]],'step':step,'started_at':started_at,'remember_options':[]}
                    approval=self.store.create_approval(task_id,'tool',public_payload)
                    self._pending_approval_calls[approval['id']]={**public_payload,'task_id':task_id,'call':self._call_payload(call),'remaining_calls':[self._call_payload(item) for item in calls[index+1:]],'history':history,'browser_review_id':exc.record['id']}
                    runtime={**(task.get('runtime') or {}),'history':history,'step':step,'started_at':started_at}
                    self.store.update_task(task_id,status='awaiting_approval',runtime=runtime)
                    self._emit(task_id,'approval.requested',{'approval':approval});self._emit(task_id,'task.status',{'status':'awaiting_approval'})
                    self._persist_metrics(task_id)
                    return True,history,step
                self._emit(task_id,'tool.finished',outcome.finished_payload(call));history.extend(outcome.output_items(call));step+=1;index+=1
                continue
            if read_only:
                if call.type == "computer":
                    raise RuntimeError("Ask mode cannot use the computer. Switch to Agent mode.")
                if entry.spec is not None and entry.spec.expose_read_only:
                    # Read-only Ask mode never enters the approval flow: mutating
                    # commands are refused inline so the model can adjust, and
                    # share_file is bounded to non-sensitive project files.
                    self._emit(task_id, "tool.started", {"call": call.public()})
                    outcome = await self._invoke_tool(entry, ctx)
                    self._emit(task_id, "tool.finished", outcome.finished_payload(call))
                    history.extend(outcome.output_items(call))
                    if outcome.cancelled:
                        raise asyncio.CancelledError
                    step += 1
                    index += 1
                    continue
            decision = entry.decision
            if decision.intent is None and call.type=='function' and call.name in {'write_file','apply_patch'}:
                from termx.auto_review import canonical_hash
                exact=canonical_hash({'tool':call.name,'cwd':ctx.cwd,'arguments':call.arguments})
                decision=self._policy_engine.decide_tool(call,ctx,fingerprint=exact,display=call.name+' · exact typed arguments',matcher={'arguments_digest':exact},base=decision,capabilities=('fs.workspace.write',),risk='consequential')
            authority=self.browser.records.get('agent-task-authority',task_id) if self.browser else None
            if authority and entry.spec and entry.spec.mutability!='read' and decision.approval_kind!='capability':
                from termx.agent.action_review import proposal
                from termx.auto_review import ReviewRequired,ActionBlocked
                envelope,validate,hard_deny=proposal(self.browser,task_id,authority,call.name or call.type,call.arguments if call.type!='computer' else {'actions':call.actions},task['cwd'],call_id=task_id+':'+call.call_id,decision=decision,read_only=read_only)
                consent=None
                if decision.auto_resolved=='allow' and decision.matched_rule_id and decision.intent:
                    matched=self.store.get_policy_rule(decision.matched_rule_id)
                    version=matched['version'] if matched else None
                    def current_consent():
                        from termx.agent.policy import PolicyDecision
                        current=self._policy_engine.evaluate(replace(decision.intent,consent_binding=self._coding_policy_binding(task_id)),PolicyDecision(False,True,'Consent revalidation','Exact operation'),ctx)
                        row=self.store.get_policy_rule(decision.matched_rule_id)
                        return bool(row and row['version']==version and current.auto_resolved=='allow' and current.matched_rule_id==decision.matched_rule_id)
                    consent=current_consent
                    base_validate=validate
                    validate=lambda:base_validate() and current_consent()
                try:
                    permit=await self.browser.review.authorize(envelope,validate=validate,hard_deny=hard_deny,existing_consent=consent,context={'task_summary':task['prompt'],'effect_summary':envelope.intended_effect})
                    self._emit(task_id,'tool.started',{'call':call.public()})
                    outcome=await self.browser.review.execute(envelope,permit['permit'],validate=validate,operation=lambda:self._invoke_tool(entry,ctx))
                except ActionBlocked as exc:
                    outcome=ToolOutcome({'refused':True,'error':exc.record['reason'],'review_id':exc.record['id']})
                except ReviewRequired as exc:
                    if exc.record['status']!='needs_user':
                        outcome=ToolOutcome({'refused':True,'error':'Action already consumed, denied or invalidated; verify outcome before proposing another action'})
                    else:
                        eligible=decision.intent is not None and envelope.intended_effect not in {'unknown','send','publish','purchase','delete','credential','privilege','export','upload'} and 'secret' not in envelope.data_labels
                        remember_options=list(self._policy_engine._remember_options(decision.intent,ctx.project_id,decision.intent.custom_agent_id)) if eligible else []
                        public={'title':'Action needs your approval','consequence':exc.record['reason'],'call':call.public(),'browser_review':exc.record,'remaining_calls':[item.public() for item in calls[index+1:]],'step':step,'started_at':started_at,'remember_options':remember_options,'policy_intent':decision.intent.to_public() if eligible else None,'policy_source':'Current typed tool / sandbox policy'}
                        approval=self.store.create_approval(task_id,'tool',public)
                        self._pending_approval_calls[approval['id']]={**public,'intent':decision.intent.to_record() if eligible else None,'approval_kind':decision.approval_kind,'task_id':task_id,'call':self._call_payload(call),'remaining_calls':[self._call_payload(item) for item in calls[index+1:]],'history':history,'browser_review_id':exc.record['id']}
                        self.store.update_task(task_id,status='awaiting_approval',runtime={**(task.get('runtime') or {}),'history':history,'step':step,'started_at':started_at})
                        self._emit(task_id,'approval.requested',{'approval':approval});self._persist_metrics(task_id)
                        return True,history,step
                self._emit(task_id,'tool.finished',outcome.finished_payload(call));history.extend(outcome.output_items(call));step+=1;index+=1
                continue
            if decision.auto_resolved and not (approved_first and index == 0):
                # A remembered rule or the Agent's own mode resolved this call.
                # The audit trail is the point — every auto-resolution is an
                # event, never a silent skip of the approval system.
                self._emit(
                    task_id,
                    "policy.matched",
                    {
                        "call": call.public(),
                        "decision": decision.auto_resolved,
                        "rule_id": decision.matched_rule_id,
                        "reason": decision.reason,
                        "intent": decision.intent.to_public() if decision.intent is not None else None,
                    },
                )
                self._emit(
                    task_id,
                    "approval.auto_resolved",
                    {
                        "call": call.public(),
                        "decision": "denied" if decision.auto_resolved == "deny" else "approved",
                        "source": decision.auto_resolved,
                        "rule_id": decision.matched_rule_id,
                        "reason": decision.reason,
                    },
                )
                if decision.auto_resolved == "deny":
                    # Refused in-band like a policy refusal: the model sees the
                    # denial and can choose a different approach; the task keeps
                    # running. No execution checkpoint — nothing ran.
                    self._emit(task_id, "tool.started", {"call": call.public()})
                    outcome = ToolOutcome(
                        result={
                            "refused": True,
                            "error": decision.consequence or decision.reason,
                            "auto_resolved": "denied",
                            "rule_id": decision.matched_rule_id,
                        }
                    )
                    self._emit(task_id, "tool.finished", outcome.finished_payload(call))
                    history.extend(outcome.output_items(call))
                    step += 1
                    index += 1
                    continue
                # "allow"/"autonomous": fall through to normal execution with
                # the checkpoint/recovery machinery untouched.
            if decision.approval_required and not (approved_first and index == 0):
                public_payload = {
                    "title": decision.reason,
                    "consequence": decision.consequence,
                    "call": call.public(),
                    "remaining_calls": [item.public() for item in calls[index + 1 :]],
                    "history": self._public_value(history),
                    "step": step,
                    "started_at": started_at,
                    # Approval API v2 — extra keys are ignored by v1 clients.
                    "policy_intent": (
                        decision.intent.to_public() if decision.intent is not None else None
                    ),
                    "policy_fingerprint": (
                        decision.intent.fingerprint if decision.intent is not None else None
                    ),
                    "approval_kind": decision.approval_kind,
                    "required_capabilities": list(decision.required_capabilities),
                    "sandbox_profile": decision.sandbox_profile,
                    "remember_options": list(decision.remember_options),
                }
                private_payload = {
                    **public_payload,
                    "task_id": task_id,
                    "call": self._call_payload(call),
                    "remaining_calls": [self._call_payload(item) for item in calls[index + 1 :]],
                    "history": history,
                    "intent": decision.intent.to_record() if decision.intent is not None else None,
                    "approval_kind": decision.approval_kind,
                    "required_capabilities": list(decision.required_capabilities),
                }
                approval = self.store.create_approval(task_id, "tool", public_payload)
                self._pending_approval_calls[approval["id"]] = private_payload
                self.store.update_task(task_id, status="awaiting_approval")
                self._emit(task_id, "approval.requested", {"approval": approval})
                self._emit(task_id, "task.status", {"status": "awaiting_approval"})
                self._persist_metrics(task_id)
                return True, history, step
            checkpoint: dict[str, Any] | None = None
            if (
                not read_only
                and entry.spec is not None
                and entry.spec.mutability != "read"
            ):
                # Side-effecting call: checkpoint prepared -> running ->
                # completed_uncommitted -> committed around execution so a host
                # restart can resume without replaying an ambiguous effect.
                checkpoint = self.store.create_checkpoint(
                    task_id,
                    kind="execution",
                    history_cursor=len(history),
                    plan_step=step,
                    pending_call_id=call.call_id,
                    side_effect_state="prepared",
                    provider_turn_id=turn_id,
                    payload={
                        "task_id": task_id,
                        "call": self._call_payload(call),
                        "remaining_calls": [
                            self._call_payload(item) for item in calls[index + 1 :]
                        ],
                        "history": history,
                        "step": step,
                        "started_at": started_at,
                    },
                )
                self._emit_checkpoint(task_id, checkpoint)
                checkpoint = self.store.update_checkpoint(
                    checkpoint["id"], side_effect_state="running"
                )
            self._emit(task_id, "tool.started", {"call": call.public()})
            outcome = await self._invoke_tool(entry, ctx)
            if checkpoint is not None:
                checkpoint = self.store.update_checkpoint(
                    checkpoint["id"],
                    side_effect_state="completed_uncommitted",
                    result={
                        "finished": outcome.finished_payload(call),
                        "output_items": outcome.output_items(call),
                    },
                )
            self._emit(task_id, "tool.finished", outcome.finished_payload(call))
            history.extend(outcome.output_items(call))
            if checkpoint is not None:
                # History is not rewritten into the payload at commit — the
                # pre-call snapshot plus the stored result reconstructs it —
                # so each call writes one history blob, not two.
                checkpoint = self.store.update_checkpoint(
                    checkpoint["id"], side_effect_state="committed"
                )
                self._emit_checkpoint(task_id, checkpoint)
            if outcome.cancelled:
                raise asyncio.CancelledError
            step += 1
            index += 1
        return False, history, step

    def _emit_checkpoint(self, task_id: str, checkpoint: dict[str, Any]) -> None:
        self._emit(task_id, "execution.checkpoint", public_checkpoint(checkpoint))

    # Crash-safe resume --------------------------------------------------

    def pending_recoveries(self) -> list[str]:
        """Task ids marked ``recovering`` at startup, awaiting a resume drive."""
        return [
            task["id"]
            for task in self.store.list_tasks(limit=500)
            if task["status"] == "recovering"
        ]

    async def recover_task(self, task_id: str, *, confirm: bool = False) -> dict[str, Any]:
        """Resume a task left in ``recovering``/``recovery_confirmation_required``.

        Safe decisions (prepared/committed/completed_uncommitted) resume
        immediately; ``running``/ambiguous checkpoints require ``confirm=True``
        and then re-execute from ``prepared`` semantics — a documented risk of
        a repeated side effect, never taken automatically.
        """
        task = self._task(task_id)
        if task["status"] not in {"recovering", "recovery_confirmation_required"}:
            raise ValueError("task is not awaiting recovery")
        checkpoint = self.store.latest_checkpoint(task_id, kind="execution")
        decision = recovery_decision(checkpoint)
        if decision == "not_resumable":
            raise ValueError("task has no resumable checkpoint")
        if decision == "confirm_required" and not confirm:
            self.store.update_task(task_id, status="recovery_confirmation_required")
            self._emit(
                task_id,
                "task.recovery.blocked",
                {
                    "message": "A side effect may already have run; confirm to resume anyway.",
                    "checkpoint_id": checkpoint["id"] if checkpoint else None,
                },
            )
            self._emit(task_id, "task.status", {"status": "recovery_confirmation_required"})
            return self._task(task_id)
        self.store.update_task(task_id, status="recovering")
        self._emit(task_id, "task.status", {"status": "recovering"})
        self._launch(task_id, self._resume_execution(task_id, checkpoint, decision))
        return self._task(task_id)

    async def _resume_execution(
        self,
        task_id: str,
        checkpoint: dict[str, Any],
        decision: str,
    ) -> None:
        try:
            payload = dict(checkpoint["payload"])
            history = list(payload.get("history") or [])
            step = int(payload.get("step") or 0)
            started_at = float(payload.get("started_at") or monotonic())
            remaining = [self._call(item) for item in payload.get("remaining_calls") or []]
            state = checkpoint["side_effect_state"]
            self._emit(
                task_id,
                "task.recovery.resumed",
                {"checkpoint_id": checkpoint["id"], "decision": decision},
            )
            if state in {"completed_uncommitted", "committed"}:
                # The side effect completed durably — reuse the stored result
                # instead of re-executing. History is the pre-call snapshot
                # plus the stored output items (call-id dedupe covers rows
                # whose payload already carried committed history).
                result = checkpoint["result"] or {}
                outputs = [
                    item
                    for item in (result.get("output_items") or [])
                    if isinstance(item, dict)
                ]
                recorded_outputs = {
                    str(item.get("call_id") or "")
                    for item in history
                    if isinstance(item, dict)
                    and str(item.get("type") or "").endswith("call_output")
                }
                history.extend(
                    item for item in outputs
                    if str(item.get("call_id") or "") not in recorded_outputs
                )
                if state == "completed_uncommitted":
                    finished = result.get("finished")
                    if isinstance(finished, dict) and not self._finished_already_recorded(
                        task_id, checkpoint["pending_call_id"]
                    ):
                        self._emit(task_id, "tool.finished", finished)
                    self.store.update_checkpoint(
                        checkpoint["id"], side_effect_state="committed"
                    )
                calls = remaining
            else:
                # prepared (or confirmed ambiguous): re-execute the call — for
                # prepared it never started; a confirmed ``running`` accepts a
                # possible repeated side effect.
                calls = [self._call(payload["call"]), *remaining]
            self.store.update_task(task_id, status="running")
            self._emit(task_id, "task.status", {"status": "running"})
            paused, history, step = await self._run_calls(
                task_id,
                calls,
                history,
                step,
                started_at,
                approved_first=state in {"prepared", "running"},
            )
            if not paused:
                await self._drive(task_id, history=history, step=step, started_at=started_at)
        except asyncio.CancelledError:
            await self._cancelled(task_id)
        except Exception as exc:
            self._fail(task_id, exc)

    def _finished_already_recorded(self, task_id: str, call_id: str | None) -> bool:
        if not call_id:
            return False
        for event in self.store.events(task_id):
            if event["type"] != "tool.finished":
                continue
            if (event.get("payload") or {}).get("call_id") == call_id:
                return True
        return False

    async def _resume_approved(self, task_id: str, payload: dict[str, Any]) -> None:
        try:
            call = self._call(payload["call"])
            remaining = [self._call(item) for item in payload.get("remaining_calls") or []]
            history = list(payload.get("history") or [])
            step = int(payload.get("step") or 0)
            started_at = float(payload.get("started_at") or monotonic())
            paused, history, step = await self._run_calls(
                task_id,
                [call, *remaining],
                history,
                step,
                started_at,
                approved_first=True,
            )
            if not paused:
                await self._drive(task_id, history=history, step=step, started_at=started_at)
        except asyncio.CancelledError:
            await self._cancelled(task_id)
        except Exception as exc:
            self._fail(task_id, exc)

    async def _invoke_tool(self, entry: Any, ctx: ToolContext) -> ToolOutcome:
        call = entry.call
        if ctx.cancel.is_set():
            raise asyncio.CancelledError
        if entry.spec is None:
            raise ValueError(f"unsupported provider tool: {call.name or call.type}")
        preset=self.store.task_agent(ctx.task)
        if preset and preset.get("tools") and call.name not in preset["tools"]:
            raise PermissionError("This tool is outside the task agent preset")
        started = monotonic()
        try:
            outcome = await self._budget_await(ctx.task_id,entry.spec.execute(call,ctx))
        except TreeTimeExceeded as exc:
            raise RuntimeError('Shared execution time exhausted during a tool. Its result may be partial; inspect the checkpoint and actual state before explicitly retrying.') from exc
        metrics = ctx.metrics
        if metrics is not None:
            metrics.record_tool(call.name or call.type, int((monotonic() - started) * 1000))
        if entry.spec.mutability != "read" and not (outcome.result or {}).get("refused"):
            engine = self._context_engines.get(ctx.task_id)
            if engine is not None:
                engine.note_mutation()
        return outcome

    def _conversation_seed(
        self, conversation: dict[str, Any] | None
    ) -> list[dict[str, Any]]:
        """Prior conversation turns as the first history item — the durable
        carry-over so a follow-up task sees earlier work in this thread."""
        if conversation is None:
            return []
        detailed = self.store.get_conversation(conversation["id"], include_turns=True)
        turns = (detailed or {}).get("turns") or []
        entries: list[dict[str, Any]] = []
        for turn in turns[-12:]:
            entry: dict[str, Any] = {
                "prompt": str(turn.get("prompt") or "")[:400],
                "mode": turn.get("mode"),
            }
            linked = turn.get("task_id")
            if linked:
                prior = self.store.get_task(str(linked))
                if prior is not None:
                    entry["task_status"] = prior["status"]
                    entry["result"] = str(
                        prior.get("result") or prior.get("error") or ""
                    )[:400]
            entries.append(entry)
        if not entries:
            return []
        return [
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "type": "conversation_context",
                        "conversation_id": conversation["id"],
                        "title": conversation.get("title") or "",
                        "prior_turns": entries,
                    }
                ),
            }
        ]

    def _coding_policy_binding(self, task_id: str) -> dict:
        browser=getattr(self,'browser',None)
        if browser is None: return {}  # legacy trusted local manager
        row=browser.records.get('agent-task-authority',task_id)
        if not row or not browser.session_valid(row['principal_id'],row['session_id'],row['policy_version']):
            return {'unbound':True}
        return {key:row[key] for key in ('principal_id','session_id','policy_version','conversation_id') if key in row}

    def _tool_context(self, task_id: str, task: dict[str, Any]) -> ToolContext:
        return ToolContext(
            task_id=task_id,
            cwd=task["cwd"],
            task=task,
            read_only=task.get("mode") == "ask",
            cancel=self._cancel.setdefault(task_id, asyncio.Event()),
            emit=lambda event_type, payload: self._emit(task_id, event_type, payload),
            store=self.store,
            manager=self,
            project_files=self._files_service(),
            project_id=self._project_id(task["cwd"]),
            metrics=self._metrics.get(task_id),
            policy_engine=self._policy_engine,
            sandbox_runner=self._sandbox_runner,
        )

    def _sandbox_runner(self, profile: str = "agent") -> Any:
        runner = self._sandbox_runners.get(profile)
        if runner is None:
            runner = self._runner_for(profile, state_dir=self.store.path.parent)
            self._sandbox_runners[profile] = runner
        return runner

    def spawn_grants(
        self, task_id: str, call_id: str, profile: str
    ) -> frozenset[str]:
        """Capability grants effective for this call's spawn: remembered
        rules (∩ backend grantable) ∪ one-shot capability approvals."""
        task = self.store.get_task(task_id) or {}
        try:
            remembered = self._policy_engine.capability_grant_set(
                profile,
                task_id=task_id or None,
                project_id=self._project_id(task["cwd"]) if task.get("cwd") else "",
                custom_agent_id=task.get("custom_agent_id"),
            )
        except Exception:
            remembered = frozenset()
        one_shot = self._one_shot_capability_grants.pop((task_id, call_id), set())
        return frozenset(remembered) | frozenset(one_shot)

    def _files_service(self) -> Any:
        if self._project_files is None:
            from termx.project_files import ProjectFiles

            self._project_files = ProjectFiles()
        return self._project_files

    def _project_id(self, cwd: str) -> str:
        # register() is an idempotent upsert keyed on the resolved path, so a
        # forgotten project is re-registered instead of leaving a stale id.
        return str(self._files_service().register(cwd)["id"])

    async def _execute_computer(
        self,
        task_id: str,
        call: ProviderCall,
        cancel: asyncio.Event,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        batch_id = uuid.uuid4().hex[:12]
        action_types = [str(action.get("type") or "") for action in call.actions]
        self._emit(
            task_id,
            "computer.action.started",
            {"batch_id": batch_id, "actions": action_types},
        )
        # Taking control back: a user takeover flips control_owner to "user";
        # the next agent batch reasserts it with a visible transition.
        if self._observation.control_owner != "agent":
            self._observation.control_owner = "agent"
            self._emit(task_id, "computer.control.changed", {"owner": "agent"})
        try:
            screenshot = await self._computer.execute(call.actions, cancel=cancel)
        except BaseException as exc:
            self._emit(
                task_id,
                "computer.action.finished",
                {
                    "batch_id": batch_id,
                    "ok": False,
                    "error": "cancelled" if isinstance(exc, asyncio.CancelledError) else str(exc),
                },
            )
            raise
        self._emit(task_id, "computer.action.finished", {"batch_id": batch_id, "ok": True})

        region = _screenshot_region(call.actions)
        cropped = crop_region(screenshot, region) if region is not None else None
        model_frame = cropped if cropped is not None else screenshot
        display_id, logical_w, logical_h = _computer_display(self._computer)
        size = jpeg_size(model_frame) or (0, 0)
        dpr = (size[0] / logical_w) if size[0] and logical_w else 1.0
        observation = self._observation.record(
            scope=task_id,
            frame=model_frame,
            display_id=display_id,
            width=size[0],
            height=size[1],
            dpr=round(dpr, 3),
            backend=_capture_backend(),
            region=region,
            region_cropped=cropped is not None,
        )
        # The model-bound frame is saved as the screenshot artifact; the full
        # display frame is preserved alongside it when a region was cropped so
        # replay never loses context.
        artifact = await asyncio.to_thread(
            self.store.save_artifact, task_id, "screenshot", "image/jpeg", model_frame
        )
        observation.artifact_id = artifact["id"]
        if cropped is not None:
            full_artifact = await asyncio.to_thread(
                self.store.save_artifact, task_id, "screenshot", "image/jpeg", screenshot
            )
            observation.extra["full_artifact_id"] = full_artifact["id"]
        metrics = self._metrics.get(task_id)
        if metrics is not None:
            metrics.record_screenshot(len(model_frame))
        self._emit(task_id, "computer.screenshot", {"artifact": artifact})
        self._emit(
            task_id,
            "computer.observation",
            {"batch_id": batch_id, "observation": observation.payload()},
        )
        image = base64.b64encode(scale_for_model(model_frame)).decode("ascii")
        if call.name == "use_computer":
            if not observation.changed:
                self._emit(
                    task_id,
                    "computer.no_change",
                    {
                        "batch_id": batch_id,
                        "observation_id": observation.id,
                        "frame_hash": observation.frame_hash,
                    },
                )
                return artifact, [
                    {
                        "type": "function_call_output",
                        "call_id": call.call_id,
                        "output": "Computer action completed.",
                    },
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "input_text",
                                "text": (
                                    f"Screen unchanged (frame matches observation "
                                    f"{observation.extra.get('previous_id') or observation.id}). "
                                    "Continue the approved task without re-inspecting."
                                ),
                            }
                        ],
                    },
                ]
            return artifact, [
                {
                    "type": "function_call_output",
                    "call_id": call.call_id,
                    "output": "Computer action completed.",
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": "Inspect the screenshot and continue the approved task.",
                        },
                        {
                            "type": "input_image",
                            "image_url": f"data:image/jpeg;base64,{image}",
                        },
                    ],
                },
            ]
        items: list[dict[str, Any]] = [
            {
                "type": "computer_call_output",
                "call_id": call.call_id,
                "output": {"type": "computer_screenshot", "image_url": f"data:image/jpeg;base64,{image}"},
                "acknowledged_safety_checks": call.safety_checks,
            }
        ]
        if not observation.changed:
            self._emit(
                task_id,
                "computer.no_change",
                {
                    "batch_id": batch_id,
                    "observation_id": observation.id,
                    "frame_hash": observation.frame_hash,
                },
            )
            # The computer_call_output contract needs an image, so the dedup
            # signal travels in an adjacent user message instead.
            items.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": (
                                f"Screen unchanged (frame matches observation "
                                f"{observation.extra.get('previous_id') or observation.id})."
                            ),
                        }
                    ],
                }
            )
        return artifact, items

    @staticmethod
    def _decide_computer(call: ProviderCall) -> PolicyDecision:
        if call.safety_checks:
            detail = "; ".join(
                str(item.get("message") or item.get("code") or "Provider flagged this action")
                for item in call.safety_checks
            )
            return PolicyDecision(
                True,
                True,
                "Provider safety check",
                detail[:500],
            )
        return evaluate_computer(call.actions)

    # Helpers ---------------------------------------------------------

    async def _share_file(
        self,
        task_id: str,
        task: dict[str, Any],
        call: ProviderCall,
    ) -> dict[str, Any]:
        raw_path = str(call.arguments.get("path") or "").strip()
        caption = str(call.arguments.get("caption") or "").strip()
        if not raw_path:
            return {"ok": False, "error": "share_file requires a path"}
        root = Path(task["cwd"]).resolve()
        candidate = Path(raw_path).expanduser()
        if not candidate.is_absolute():
            candidate = root / candidate
        try:
            resolved = candidate.resolve(strict=True)
        except OSError:
            return {"ok": False, "error": f"File not found: {raw_path}"}
        if resolved != root and root not in resolved.parents:
            return {"ok": False, "error": "share_file stays inside the project folder"}
        if not resolved.is_file():
            return {"ok": False, "error": f"Not a file: {raw_path}"}
        if is_sensitive_path(str(resolved)) or is_sensitive_path(raw_path):
            return {
                "ok": False,
                "refused": True,
                "error": "Refused: credential, key, and environment files cannot be shared",
            }
        size = resolved.stat().st_size
        if size > _SHARE_MAX_BYTES:
            return {"ok": False, "error": f"File too large to share ({size} bytes, max {_SHARE_MAX_BYTES})"}
        data = await asyncio.to_thread(resolved.read_bytes)
        mime = mimetypes.guess_type(resolved.name)[0] or "application/octet-stream"
        artifact = await asyncio.to_thread(self.store.save_artifact, task_id, "media", mime, data)
        self._emit(
            task_id,
            "agent.media",
            {"artifact": artifact, "name": resolved.name, "caption": caption, "call_id": call.call_id},
        )
        return {
            "ok": True,
            "shared": resolved.name,
            "artifact": {"id": artifact["id"], "mime": artifact["mime"], "size": artifact["size"]},
        }

    def _subagent_handles(self, task_id: str) -> dict[str, _SubagentHandle]:
        """Live handle registry for one parent task.

        Rebuilt lazily from the durable tasks table so handles survive a host
        restart: terminal children come back already done, still-active ones
        (re-driven by crash recovery) get a fresh relay watcher.
        """
        handles = self._subagents.setdefault(task_id, {})
        try:
            children = self.store.children(task_id)
        except (KeyError, ValueError):
            return handles
        for child in children:
            child_id = child["id"]
            if child_id in handles:
                continue
            handle = _SubagentHandle(parent_id=task_id, child_id=child_id)
            handles[child_id] = handle
            if child["status"] in ACTIVE_STATUSES:
                self._attach_watcher(task_id, handle, child)
            else:
                handle.status = child["status"]
                handle.result = str(child.get("result") or child.get("error") or "")
                handle.done.set()
        return handles

    def _attach_watcher(
        self,
        parent_id: str,
        handle: _SubagentHandle,
        child: dict[str, Any],
    ) -> None:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=200)
        self._subscribers[handle.child_id].add(queue)
        handle.queue = queue
        child_limits = child.get("limits") or {}
        try:
            max_seconds = float(child_limits.get("max_seconds") or _SUBAGENT_MAX_SECONDS)
        except (TypeError, ValueError):
            max_seconds = float(_SUBAGENT_MAX_SECONDS)
        elapsed = max(0.0, time() - float(child.get("created_at") or time()))
        handle.deadline = monotonic() + max(30.0, min(max_seconds, _SUBAGENT_MAX_SECONDS) - elapsed)
        if handle.parent_cancel is None:
            handle.parent_cancel = self._cancel.get(parent_id) or asyncio.Event()
        handle.watcher = asyncio.create_task(
            self._watch_child(parent_id, handle), name=f"termx-subagent-{handle.child_id[:8]}"
        )

    async def _spawn_subagent(
        self,
        task_id: str,
        task: dict[str, Any],
        call: ProviderCall,
        cancel: asyncio.Event,
    ) -> dict[str, Any]:
        prompt = str(call.arguments.get("task") or "").strip()
        if not prompt:
            return {"ok": False, "error": "spawn_subagent requires a task"}
        if task.get("parent_id"):
            return {
                "ok": False,
                "error": "sub-agents cannot spawn sub-agents (max depth 1)",
            }
        agent = str(call.arguments.get("agent") or "").strip() or "Sub-agent"
        instructions = str(call.arguments.get("instructions") or "").strip()
        child_prompt = prompt
        if instructions:
            child_prompt = (
                f'You are the "{agent}" sub-agent. Follow these instructions:\n'
                f"{instructions}\n\nTask: {prompt}"
            )
        parent_limits = task["limits"]
        handles = self._subagent_handles(task_id)
        running = sum(1 for handle in handles.values() if not handle.done.is_set())
        max_parallel = int(parent_limits.get("max_parallel_subagents") or DEFAULT_LIMITS["max_parallel_subagents"])
        if running >= max_parallel:
            return {
                "ok": False,
                "error": f"sub-agent parallel limit reached ({running}/{max_parallel} running)",
                "hint": "call await_subagents to free a slot, or raise max_parallel_subagents",
            }
        max_total = int(parent_limits.get("max_subagents_total") or DEFAULT_LIMITS["max_subagents_total"])
        if len(handles) >= max_total:
            return {
                "ok": False,
                "error": f"sub-agent total limit reached ({len(handles)}/{max_total} spawned)",
            }

        def attach(child_id: str, queue: asyncio.Queue[dict[str, Any]]) -> None:
            self._subscribers[child_id].add(queue)
            if self.browser:
                authority=self.browser.records.get('agent-task-authority',task_id)
                if authority:self.browser.records.put('agent-task-authority',child_id,{**authority,'id':child_id,'delegated_by':task_id})

        child_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=200)
        child_mode='ask' if str(call.arguments.get('mode') or '').strip().lower()=='ask' else 'agent'
        child_cwd=task['cwd'];child_execution='direct';isolation={'kind':'read-only','path':child_cwd}
        snapshot_path=None
        if child_mode=='agent':
            if await asyncio.to_thread(worktrees.git_root,child_cwd):
                child_execution='worktree'
                child_prompt+='\n\nExecution uses an isolated Git checkout at the project HEAD. Parent uncommitted files are not shared; request missing context explicitly. Keep changes in this checkout for review.'
            else:
                from termx.agent.child_isolation import snapshot
                snapshot_path=self.store.artifact_dir/'delegations'/uuid.uuid4().hex
                try:
                    isolation=await asyncio.to_thread(snapshot,Path(child_cwd),snapshot_path,excluded=[self.store.path,self.store.artifact_dir])
                except (ValueError,OSError) as exc:return {'ok':False,'error':str(exc)}
                child_cwd=str(snapshot_path)
                child_prompt+='\n\nExecution uses an independent project snapshot. Keep all edits here; they are retained for review and never automatically copied into the parent project.'
        child = await self.create_task(
            prompt=child_prompt,
            cwd=child_cwd,
            provider_id=task["provider_id"],
            limits={
                "max_steps": min(24, parent_limits["max_steps"]),
                "max_seconds": min(900, parent_limits["max_seconds"]),
                "shell_timeout_s": parent_limits["shell_timeout_s"],
            },
            mode=child_mode,
            execution_mode=child_execution,
            model=str(task.get("model") or "") or None,
            cancel=cancel,
            on_created=lambda child_id: attach(child_id, child_queue),
            parent_id=task_id,
            custom_agent_id=task.get("custom_agent_id"),
            custom_agent_snapshot=self.store.task_agent(task),
        )
        child_id = child["id"]
        if child_execution=='worktree':
            record=self.store.task_worktree(child_id)
            isolation={'kind':'worktree','path':child['cwd'],'branch':record['branch'],'base_ref':record['base_ref'],'base_path':task['cwd'],'automatic_apply':False}
        self._emit(child_id,'task.isolation',isolation)
        handle = _SubagentHandle(
            parent_id=task_id,
            child_id=child_id,
            call_id=call.call_id,
            agent=agent,
            queue=child_queue,
            parent_cancel=cancel,
        )
        handle.deadline = monotonic() + min(
            float(parent_limits["max_seconds"]), _SUBAGENT_MAX_SECONDS
        )
        handles[child_id] = handle
        if cancel.is_set():
            self.unsubscribe(child_id, child_queue)
            self.cancel(child_id)
            handle.status = "cancelled"
            handle.done.set()
            raise asyncio.CancelledError
        self._emit(
            task_id,
            "subagent.started",
            {"call_id": call.call_id, "agent": agent, "task": prompt, "child_id": child_id,'isolation':isolation},
        )
        if child["status"] in {"completed", "failed", "cancelled"}:
            self.unsubscribe(child_id, child_queue)
            handle.status = child["status"]
            handle.result = str(child.get("error") or child.get("result") or "")
            handle.done.set()
            self._emit(
                task_id,
                "subagent.finished",
                {
                    "call_id": call.call_id,
                    "child_id": child_id,
                    "agent": agent,
                    "status": handle.status,
                    "result": handle.result,
                },
            )
            return {
                "ok": handle.status == "completed",
                "status": handle.status,
                "result": handle.result[:4000],
                "task_id": child_id,
            }
        # The parent's approved plan covers the delegation itself, so the child's
        # plan is approved on its behalf; its consequential tool checks are
        # escalated to the parent for the user to decide.
        handle.watcher = asyncio.create_task(
            self._watch_child(task_id, handle), name=f"termx-subagent-{child_id[:8]}"
        )
        await self._auto_approve(child_id, kind="plan")
        return {"ok": True, "status": "running", "task_id": child_id}

    async def _watch_child(self, parent_id: str, handle: _SubagentHandle) -> None:
        """Relay a child's events onto the parent's stream until it terminates.

        Independent of any single tool call so fan-out children keep streaming
        while the parent does other work; consequential child actions are still
        escalated to the parent's approval queue.
        """
        child_id = handle.child_id
        queue = handle.queue
        assert queue is not None
        status = "failed"
        result = ""
        try:
            while True:
                if handle.parent_cancel is not None and handle.parent_cancel.is_set():
                    self.cancel(child_id)
                waiting_for_user = self._task(child_id)['status'] == 'awaiting_approval'
                wait_started = monotonic()
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=0.4)
                except asyncio.TimeoutError:
                    current = self._task(child_id)
                    if current["status"] not in ACTIVE_STATUSES:
                        status = current["status"]
                        result = str(current.get("result") or current.get("error") or "")
                        break
                    if current['status'] != 'awaiting_approval' and monotonic() >= handle.deadline:
                        self.cancel(child_id)
                    continue
                finally:
                    # The watchdog must not cancel a child while the same
                    # explicit human wait is paused by the shared meter.
                    if waiting_for_user:
                        handle.deadline += monotonic() - wait_started
                event_type = event["type"]
                payload = event.get("payload") or {}
                if event_type == "approval.requested":
                    approval = payload.get("approval") or {}
                    if approval.get("kind") in {"tool", "budget"} and approval.get("id"):
                        self._escalate_child_approval(parent_id, child_id, handle.agent, approval)
                self._emit(
                    parent_id,
                    "subagent.event",
                    {
                        "call_id": handle.call_id,
                        "child_id": child_id,
                        "agent": handle.agent,
                        "type": event_type,
                        "payload": _slim_payload(payload),
                    },
                )
                if event_type in {"task.completed", "task.failed", "task.cancelled"}:
                    status = {
                        "task.completed": "completed",
                        "task.failed": "failed",
                        "task.cancelled": "cancelled",
                    }[event_type]
                    result = str(payload.get("result") or payload.get("message") or "")
                    break
        finally:
            self.unsubscribe(child_id, queue)
            self._drop_pending_approvals(parent_id, child_id=child_id)
            handle.status = status
            handle.result = result
            self._unpause_if_idle(parent_id)
            self._emit(
                parent_id,
                "subagent.finished",
                {
                    "call_id": handle.call_id,
                    "child_id": child_id,
                    "agent": handle.agent,
                    "status": status,
                    "result": result,
                },
            )
            handle.done.set()

    def _subagent_targets(
        self,
        task_id: str,
        call: ProviderCall,
    ) -> tuple[dict[str, _SubagentHandle], list[str]]:
        """Resolve named children (task_id/task_ids args) against the parent's
        handle registry; empty selection targets every child of this parent."""
        handles = self._subagent_handles(task_id)
        raw: list[str] = []
        single = str(call.arguments.get("task_id") or "").strip()
        if single:
            raw.append(single)
        listed = call.arguments.get("task_ids")
        if isinstance(listed, list):
            raw.extend(str(item).strip() for item in listed if str(item).strip())
        if not raw:
            return dict(handles), []
        targets: dict[str, _SubagentHandle] = {}
        missing: list[str] = []
        for child_id in raw:
            handle = handles.get(child_id)
            if handle is None:
                missing.append(child_id)
            else:
                targets[child_id] = handle
        return targets, missing

    @staticmethod
    def _subagent_summary(handle: _SubagentHandle) -> dict[str, Any]:
        entry: dict[str, Any] = {
            "child_id": handle.child_id,
            "agent": handle.agent,
            "status": handle.status,
            "running": not handle.done.is_set(),
        }
        if handle.done.is_set():
            entry["result"] = handle.result[:4000]
        return entry

    async def _await_subagents(
        self,
        task_id: str,
        call: ProviderCall,
        cancel: asyncio.Event,
    ) -> dict[str, Any]:
        targets, missing = self._subagent_targets(task_id, call)
        raw_timeout = call.arguments.get("timeout_s")
        try:
            timeout_s = float(raw_timeout) if raw_timeout is not None else 300.0
        except (TypeError, ValueError):
            timeout_s = 300.0
        timeout_s = min(600.0, max(0.0, timeout_s))
        pending = [handle for handle in targets.values() if not handle.done.is_set()]
        timed_out = False
        if pending and timeout_s > 0:
            try:
                await _race_cancel(
                    asyncio.wait_for(
                        asyncio.gather(
                            *(handle.done.wait() for handle in pending), return_exceptions=True
                        ),
                        timeout=timeout_s,
                    ),
                    cancel,
                )
            except asyncio.TimeoutError:
                timed_out = True
        elif pending:
            timed_out = True
        children = {
            child_id: self._subagent_summary(handle) for child_id, handle in targets.items()
        }
        self._emit(
            task_id,
            "subagent.awaited",
            {
                "call_id": call.call_id,
                "timed_out": timed_out,
                "children": {child_id: entry["status"] for child_id, entry in children.items()},
            },
        )
        return {
            "ok": not missing and not timed_out,
            "timed_out": timed_out,
            "children": children,
            "missing": missing,
        }

    def _subagent_statuses(self, task_id: str, call: ProviderCall) -> dict[str, Any]:
        targets, missing = self._subagent_targets(task_id, call)
        return {
            "ok": not missing,
            "children": {
                child_id: self._subagent_summary(handle) for child_id, handle in targets.items()
            },
            "missing": missing,
        }

    def _cancel_subagent(self, task_id: str, call: ProviderCall) -> dict[str, Any]:
        targets, missing = self._subagent_targets(task_id, call)
        cancelled: list[str] = []
        already_finished: list[str] = []
        for child_id, handle in targets.items():
            if handle.done.is_set():
                already_finished.append(child_id)
                continue
            self.cancel(child_id)
            cancelled.append(child_id)
            self._emit(
                task_id,
                "subagent.cancelled",
                {"call_id": call.call_id, "child_id": child_id, "agent": handle.agent},
            )
        return {
            "ok": not missing,
            "cancelled": cancelled,
            "already_finished": already_finished,
            "missing": missing,
        }

    def _escalate_child_approval(
        self,
        task_id: str,
        child_id: str,
        agent: str,
        approval: dict[str, Any],
    ) -> None:
        inner = dict(approval.get("payload") or {})
        inner.pop("history", None)
        inner.pop("remaining_calls", None)
        public_payload = {
            **inner,
            "title": f'{agent}: {inner.get("title") or "Consequential action"}',
            "child_id": child_id,
            "child_approval_id": approval["id"],
        }
        parent_approval = self.store.create_approval(task_id, "budget" if approval['kind']=='budget' else "tool", public_payload)
        self._pending_approval_calls[parent_approval["id"]] = {
            "task_id": task_id,
            "child_id": child_id,
            "child_approval_id": approval["id"],
        }
        self.tree_budget.stop(task_id)
        self.store.update_task(task_id, status="awaiting_approval")
        self._emit(task_id, "approval.requested", {"approval": parent_approval})
        self._emit(task_id, "task.status", {"status": "awaiting_approval"})

    async def _auto_approve(self, task_id: str, *, kind: str | None = None) -> None:
        approvals = self.store.approvals(task_id)
        for approval in approvals:
            if approval["status"] != "pending":
                continue
            if kind is not None and approval["kind"] != kind:
                continue
            try:
                await self.resolve_approval(task_id, approval["id"], "approved")
            except (KeyError, ValueError):
                continue

    def _upload_message(self, task_id: str, artifact_ids: list[str]) -> dict[str, Any]:
        parts: list[dict[str, Any]] = [{"type": "input_text", "text": "The user attached these images to the message."}]
        for artifact_id in artifact_ids:
            artifact = self.store.get_artifact(task_id, artifact_id)
            if artifact is None or not str(artifact.get("mime") or "").startswith("image/"):
                continue
            encoded = base64.b64encode(Path(artifact["path"]).read_bytes()).decode("ascii")
            parts.append({"type": "input_image", "image_url": f"data:{artifact['mime']};base64,{encoded}"})
        return {"role": "user", "content": parts}

    def _emit(self, task_id: str, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        event = self.store.append_event(task_id, event_type, payload)
        for listener in tuple(self._listeners):
            try:
                listener(task_id, event)
            except Exception:  # noqa: BLE001 - listeners must never break emit
                pass
        for queue in tuple(self._subscribers.get(task_id, ())):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                try:
                    queue.get_nowait()
                    queue.put_nowait(event)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    pass
        return event

    async def _budget_await(self,task_id,operation):
        pending=asyncio.ensure_future(operation)
        try:
            while not pending.done():
                shared=await asyncio.to_thread(self.tree_budget.snapshot,task_id)
                if shared['reason']=='shared_max_seconds':raise TreeTimeExceeded('Shared active execution seconds exhausted')
                await asyncio.wait({pending},timeout=.1)
            return await pending
        finally:
            if not pending.done():
                pending.cancel();await asyncio.gather(pending,return_exceptions=True)

    async def _meter_worker(self,task_id,operation):
        self.tree_budget.start(task_id)
        try:return await operation
        finally:self.tree_budget.stop(task_id)

    def _launch(self, task_id: str, coroutine: Any) -> None:
        current = self._workers.get(task_id)
        if current is not None and not current.done():
            raise ValueError("task is already running")
        worker = asyncio.create_task(self._meter_worker(task_id,coroutine), name=f"termx-agent-{task_id[:8]}")
        self._workers[task_id] = worker
        def finished(done: asyncio.Task[None]) -> None:
            if self._workers.get(task_id) is done:
                self._workers.pop(task_id, None)
        worker.add_done_callback(finished)

    def _provider(self, provider_id: str) -> dict[str, Any]:
        provider = self.store.get_provider(provider_id)
        if provider is None:
            raise KeyError(provider_id)
        return provider

    def _adapter(self, provider: dict[str, Any]) -> ProviderAdapter:
        if provider["kind"] == "chatgpt":
            from termx.agent.providers import ChatGPTResponsesAdapter
            return ChatGPTResponsesAdapter(accounts=self.chatgpt, account_id=provider["id"],
                                           model=provider["model"],
                                           timeout_s=self._settings().provider_timeout_s if self._settings else 300)
        key = self.credentials.get(provider["id"]) or ""
        return self._adapter_factory(provider, key)

    def _default_adapter(self, provider: dict[str, Any], key: str) -> ProviderAdapter:
        adapter = OpenAIResponsesAdapter(
            base_url=provider["base_url"],
            model=provider["model"],
            api_key=key,
            capabilities=provider["capabilities"],
            native_computer=provider["kind"] == "openai",
            timeout_s=self._settings().provider_timeout_s if self._settings else 300,
        )
        adapter.client = self._http.client_for(
            provider["id"],
            timeout_s=adapter.timeout_s,
            headers=adapter.request_headers(),
        )
        return adapter

    def _task(self, task_id: str) -> dict[str, Any]:
        task = self.store.get_task(task_id)
        if task is None:
            raise KeyError(task_id)
        return task

    def _limits(self, value: dict[str, Any] | None) -> dict[str, int]:
        return resolve_limits(value, self._settings().limits if self._settings else None)

    @staticmethod
    def _call(value: dict[str, Any]) -> ProviderCall:
        return ProviderCall(
            type=str(value.get("type") or ""),
            call_id=str(value.get("call_id") or ""),
            name=str(value.get("name") or ""),
            arguments=value.get("arguments") if isinstance(value.get("arguments"), dict) else {},
            actions=value.get("actions") if isinstance(value.get("actions"), list) else [],
            safety_checks=value.get("safety_checks") if isinstance(value.get("safety_checks"), list) else [],
        )

    @staticmethod
    def _call_payload(call: ProviderCall) -> dict[str, Any]:
        return {
            "type": call.type,
            "call_id": call.call_id,
            "name": call.name,
            "arguments": call.arguments,
            "actions": call.actions,
            "safety_checks": call.safety_checks,
        }

    @staticmethod
    def _public_value(value: Any) -> Any:
        if isinstance(value, str):
            return redact(value)
        if isinstance(value, list):
            return [AgentManager._public_value(item) for item in value]
        if isinstance(value, dict):
            return {
                str(key): AgentManager._public_value(item)
                for key, item in value.items()
            }
        return value

    def _has_pending_approvals(self, task_id: str) -> bool:
        return any(
            payload.get("task_id") == task_id
            for payload in self._pending_approval_calls.values()
        )

    def _unpause_if_idle(self, task_id: str) -> None:
        """Restore 'running' only when nothing remains to approve — the
        task's own pending tool approvals must stay resolvable."""
        if self._task(task_id)["status"] != "awaiting_approval":
            return
        if self._has_pending_approvals(task_id):
            return
        if task_id in self._workers:self.tree_budget.start(task_id)
        self.store.update_task(task_id, status="running")
        self._emit(task_id, "task.status", {"status": "running"})

    def _drop_pending_approvals(
        self, task_id: str, *, child_id: str | None = None, keep_escalations: bool = False
    ) -> None:
        for approval_id, payload in list(self._pending_approval_calls.items()):
            if payload["task_id"] != task_id:
                continue
            if child_id is not None and payload.get("child_id") != child_id:
                continue
            if keep_escalations and "child_approval_id" in payload:
                continue
            self._pending_approval_calls.pop(approval_id, None)

    def _finalize_worktree(self, task_id: str) -> None:
        """Record the terminal head SHA and mark the worktree inspectable."""
        record = self.store.task_worktree(task_id)
        if record is None or record["status"] != "active":
            return
        try:
            head = worktrees.head_sha(record["worktree_path"] or "")
        except Exception:
            head = None
        if head:
            record = self.store.update_task_worktree(task_id, head_sha=head) or record
        self._emit(task_id, "task.worktree.final", {"worktree": record})

    def _persist_metrics(self, task_id: str) -> None:
        metrics = self._metrics.get(task_id)
        if metrics is None:
            return
        snapshot = metrics.snapshot()
        try:
            self.store.update_task(task_id, metrics=snapshot)
        except (KeyError, ValueError):
            return
        # Publish on every terminal transition (deny/cancel/fail go through
        # _finish_state) — the completed path already emitted the same event.
        self._emit(task_id, "task.metrics", {"metrics": snapshot})

    def _finish_state(self, task_id: str) -> None:
        """Persist final metrics and drop all in-memory per-task state.

        Runs on every terminal transition so tasks denied while awaiting
        approval (whose worker already exited) are reclaimed too.
        """
        self._persist_metrics(task_id)
        self._context_engines.pop(task_id, None)
        self._metrics.pop(task_id, None)
        self._steering.pop(task_id, None)
        # Task-scoped remembered rules die with their task — they can never
        # leak into a later task in the same project.
        self.store.expire_task_policy_rules(task_id)
        self._finalize_worktree(task_id)
        # Escalated child approvals stay resolvable past a terminal parent — the
        # child is still waiting on the user's decision and its watcher drops
        # the entry when it finishes.
        self._drop_pending_approvals(task_id, keep_escalations=True)

    async def _cancelled(self, task_id: str) -> None:
        if self._task(task_id)["status"] == "cancelled":
            return
        await self._computer.release_all()
        self._mark_cancelled(task_id)

    def _mark_cancelled(self, task_id: str) -> None:
        if self._task(task_id)["status"] == "cancelled":
            return
        self._finish_state(task_id)
        self.store.update_task(task_id, status="cancelled", error="Cancelled by user")
        self._emit(task_id, "task.cancelled", {"message": "Cancelled by user"})

    @staticmethod
    async def _provider_turn(
        adapter: ProviderAdapter,
        cancel: asyncio.Event,
        *,
        prompt: str,
        cwd: str,
        manifest: dict[str, Any],
        input_items: list[dict[str, Any]] | None,
        allow_computer: bool,
        read_only: bool,
    ) -> ProviderTurn:
        return await _race_cancel(
            adapter.turn(
                prompt=prompt,
                cwd=cwd,
                manifest=manifest,
                input_items=input_items,
                allow_computer=allow_computer,
                read_only=read_only,
            ),
            cancel,
        )

    def _fail(self, task_id: str, exc: Exception) -> None:
        if isinstance(exc, ProviderError):
            message = str(exc)
        elif isinstance(exc, (ValueError, RuntimeError, OSError)):
            message = str(exc)
        else:
            message = "Agent run failed"
        self._finish_state(task_id)
        self.store.update_task(task_id, status="failed", error=message)
        self._emit(task_id, "task.failed", {"message": message})


def _snapshot_event(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Slim snapshot summary for the durable event log (omits the file list)."""
    event = {key: value for key, value in snapshot.items() if key != "files"}
    event["file_count"] = len(snapshot.get("files") or [])
    return event


_IMAGE_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
_SHARE_MAX_BYTES = 16 * 1024 * 1024
_SUBAGENT_MAX_SECONDS = 1800


def _screenshot_region(actions: list[dict[str, Any]]) -> dict[str, int] | None:
    """Region requested by the last screenshot action, normalized to pixels."""
    for action in reversed(actions):
        if str(action.get("type") or "") != "screenshot":
            continue
        region = action.get("region")
        if not isinstance(region, dict):
            return None
        try:
            return {
                "x": max(0, int(region.get("x") or 0)),
                "y": max(0, int(region.get("y") or 0)),
                "width": max(1, int(region.get("width") or region.get("w") or 0)),
                "height": max(1, int(region.get("height") or region.get("h") or 0)),
            }
        except (TypeError, ValueError):
            return None
    return None


def _computer_display(computer: Any) -> tuple[str | None, float, float]:
    display_fn = getattr(computer, "_display", None)
    if not callable(display_fn):
        return None, 0.0, 0.0
    try:
        return display_fn()
    except Exception:
        return None, 0.0, 0.0


def _capture_backend() -> str:
    try:
        from termx.desktop.capabilities import probe_desktop

        return str(probe_desktop().capture_backend or "unknown")
    except Exception:
        return "unknown"


@dataclass
class _SubagentHandle:
    """Parent-side handle for an async child task (AG2-012).

    The watcher keeps relaying sub-agent events and approval escalations while
    the parent's own driver does other work; `done` is what await_subagents
    waits on, and status/result hold the terminal outcome for status queries.
    """

    parent_id: str
    child_id: str
    call_id: str = ""
    agent: str = "Sub-agent"
    queue: asyncio.Queue[dict[str, Any]] | None = None
    watcher: asyncio.Task | None = None
    parent_cancel: asyncio.Event | None = None
    deadline: float = 0.0
    status: str = "running"
    result: str = ""
    done: asyncio.Event = field(default_factory=asyncio.Event)


async def _race_cancel(coroutine: Awaitable[T], cancel: asyncio.Event) -> T:
    """Await `coroutine`, aborting it with CancelledError as soon as `cancel` is set."""
    work = asyncio.ensure_future(coroutine)
    cancelled = asyncio.create_task(cancel.wait())
    try:
        done, _ = await asyncio.wait({work, cancelled}, return_when=asyncio.FIRST_COMPLETED)
        if cancelled in done:
            work.cancel()
            await asyncio.gather(work, return_exceptions=True)
            raise asyncio.CancelledError
        return await work
    finally:
        cancelled.cancel()
        if not work.done():
            work.cancel()
        await asyncio.gather(cancelled, work, return_exceptions=True)


def _slim_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Drop transcript-sized fields before forwarding a child event to the parent stream."""
    slim = {
        str(key): value
        for key, value in payload.items()
        if key not in {"history", "remaining_calls"}
    }
    approval = slim.get("approval")
    if isinstance(approval, dict):
        inner = dict(approval.get("payload") or {})
        inner.pop("history", None)
        inner.pop("remaining_calls", None)
        slim["approval"] = {**approval, "payload": inner}
    return slim


def _resolve_model(provider: dict[str, Any], requested: str | None) -> str:
    models = configured_models(str(provider.get("model") or ""))
    if not models:
        raise ValueError("provider has no model configured")
    chosen = (requested or "").strip()
    if not chosen:
        return models[0]
    if chosen not in models:
        raise ValueError("model is not configured on this provider")
    return chosen


def _decode_images(attachments: list[dict[str, Any]] | None) -> list[tuple[str, str, bytes]]:
    images: list[tuple[str, str, bytes]] = []
    for item in attachments or []:
        mime = str(item.get("mime") or "")
        if mime not in _IMAGE_TYPES:
            raise ValueError("only JPEG, PNG, GIF, and WebP images can be attached")
        try:
            raw = base64.b64decode(str(item.get("data") or ""), validate=True)
        except Exception as exc:
            raise ValueError("image data is not valid base64") from exc
        if not raw or len(raw) > 2_000_000:
            raise ValueError("each image must be under 2 MB")
        name = str(item.get("name") or "image")[:180]
        images.append((name, mime, raw))
    if len(images) > 4:
        raise ValueError("attach at most 4 images")
    return images
