"""Policy precedence engine.

Evaluation order (deterministic, per the architecture handoff):

1. hard deny / sandbox impossibility — a required capability the sandbox can
   never grant (UNGRANTABLE set) blocks outright; a grantable-but-not-granted
   capability produces a separate capability ask — a remembered *execution*
   allow never punches through a missing sandbox capability.
2. explicit remembered deny — any matching deny wins over remembered allows.
3. narrower remembered allow (task > custom_agent > project).
4. custom-agent ``approval_mode`` — `autonomous` auto-approves only
   sandbox-contained safe/consequential actions.
5. Termx safe defaults — the regex policy layer (intent/UX, not a boundary).
6. ask the user.
"""
from __future__ import annotations

from dataclasses import replace
from time import time
from typing import TYPE_CHECKING, Any, Callable

from termx.agent.policies import fingerprint as fp
from termx.agent.policies.defaults import (
    AUTONOMOUS_BLOCKED_RISKS,
    UNGRANTABLE_CAPABILITIES,
    intent_risk,
    shell_capabilities,
    tool_capabilities,
)
from termx.agent.policies.models import REMEMBER_SCOPES, SCOPE_ORDER, PolicyIntent

if TYPE_CHECKING:
    from termx.agent.policy import PolicyDecision
    from termx.agent.providers import ProviderCall
    from termx.agent.tools.registry import ToolContext


def _covers(granted: frozenset[str], capability: str) -> bool:
    """Namespace wildcard matching shared with SandboxCapabilities.covers:
    "ns:any" covers "ns", "ns:x", "ns.deeper", "ns.deeper:x"."""
    if capability in granted or "*:any" in granted:
        return True
    ns = capability.split(":", 1)[0]
    parts = ns.split(".")
    for depth in range(len(parts), 0, -1):
        if ".".join(parts[:depth]) + ":any" in granted:
            return True
    return False


class PolicyEngine:
    """Evaluates tool-call intents against remembered rules, the sandbox
    capability envelope, and the custom agent's approval mode."""

    def __init__(
        self,
        store: Any,
        *,
        envelope: Callable[[str], frozenset[str]] | None = None,
        grantable: Callable[[str], frozenset[str]] | None = None,
        binding_lookup: Callable[[str], dict] | None = None,
    ) -> None:
        self._store = store
        self._binding_lookup = binding_lookup or (lambda task_id: {})
        # profile -> capabilities the sandbox backend currently provides.
        # Default is the host backend: unrestricted (truthful, not "secure").
        self._envelope = envelope or (lambda _profile: frozenset({"*:any"}))
        # profile -> capabilities the backend can *additionally* provide when a
        # remembered capability rule grants them. Defaults to the envelope
        # itself: a backend that can't dynamically extend grants makes stored
        # capability rules unable to invent powers the runner lacks.
        self._grantable = grantable or self._envelope

    # -- decider entry points (wired into ToolSpec.decide) -------------------

    def decide_shell(
        self,
        call: "ProviderCall",
        ctx: "ToolContext",
        command: str,
        base: "PolicyDecision",
    ) -> "PolicyDecision":
        intent = fp.shell_intent(command, call, ctx)
        intent = self._enrich(
            intent,
            ctx,
            capabilities=shell_capabilities(command),
            risk=intent_risk(intent, base.reason if base.approval_required else None),
        )
        return self.evaluate(intent, base, ctx)

    def decide_tool(
        self,
        call: "ProviderCall",
        ctx: "ToolContext",
        *,
        fingerprint: str,
        display: str,
        matcher: dict[str, Any],
        base: "PolicyDecision",
        capabilities: tuple[str, ...] | None = None,
        risk: str | None = None,
    ) -> "PolicyDecision":
        intent = fp.tool_intent(call, ctx, fingerprint=fingerprint, display=display, matcher=matcher)
        intent = self._enrich(
            intent,
            ctx,
            capabilities=capabilities or tool_capabilities(call.name or ""),
            risk=risk or intent_risk(intent, base.reason if base.approval_required else None),
        )
        return self.evaluate(intent, base, ctx)

    def _enrich(
        self,
        intent: PolicyIntent,
        ctx: "ToolContext",
        *,
        capabilities: tuple[str, ...],
        risk: str,
    ) -> PolicyIntent:
        custom_agent_id = intent.custom_agent_id or (getattr(ctx, "task", None) or {}).get(
            "custom_agent_id"
        )
        profile = "agent"
        if custom_agent_id:
            custom = self._store.task_agent(getattr(ctx,"task",None) or {},custom_agent_id)
            if custom is not None and custom.get("sandbox_profile"):
                profile = str(custom["sandbox_profile"])
        return replace(
            intent,
            custom_agent_id=custom_agent_id,
            required_capabilities=capabilities,
            risk_class=risk,
            sandbox_profile=profile,
            consent_binding=self._binding_lookup(intent.task_id),
        )

    # -- precedence ----------------------------------------------------------

    def evaluate(
        self,
        intent: PolicyIntent,
        base: "PolicyDecision",
        ctx: "ToolContext",
    ) -> "PolicyDecision":
        from termx.agent.policy import PolicyDecision as _PD

        project_id = intent.project_id or getattr(ctx, "project_id", "") or ""
        custom_agent_id = intent.custom_agent_id

        scopes=[('host',''),('task',intent.task_id),('project',project_id)]
        if custom_agent_id: scopes.append(('custom_agent',custom_agent_id))
        if intent.consent_binding.get('conversation_id'): scopes.append(('conversation',intent.consent_binding['conversation_id']))
        for rule in self._store.capability_rules(scopes=scopes):
            if rule['effect']=='deny' and (not rule.get('sandbox_profile') or rule['sandbox_profile']==intent.sandbox_profile) and self._binding_matches(rule,intent.consent_binding) and any(_covers(frozenset(rule['capabilities']),cap) for cap in intent.required_capabilities):
                return _PD(False,False,'Denied by capability policy','A matching capability deny cannot be overridden by remembered consent',intent=intent,auto_resolved='deny',matched_rule_id=rule['id'])

        # 1. Sandbox capability check — before every other rule.
        envelope = self._envelope(intent.sandbox_profile)
        missing = [
            cap for cap in intent.required_capabilities if not _covers(envelope, cap)
        ]
        if missing:
            ungrantable = [c for c in missing if c in UNGRANTABLE_CAPABILITIES]
            if ungrantable:
                # The sandbox can never grant this — hard deny, no ask path.
                # An ungrantable requirement dominates even when grantable
                # capabilities are also missing: asking would imply the spawn
                # could ever succeed.
                return _PD(
                    False,
                    False,
                    "Capability unavailable",
                    f"Sandbox profile {intent.sandbox_profile} cannot grant {', '.join(ungrantable)}",
                    intent=intent,
                    auto_resolved="deny",
                    required_capabilities=intent.required_capabilities,
                    sandbox_profile=intent.sandbox_profile,
                )
            remembered_caps = self._remembered_capabilities(
                intent.sandbox_profile,
                task_id=intent.task_id,
                project_id=project_id,
                custom_agent_id=custom_agent_id,
                consent_binding=intent.consent_binding,
            )
            still_missing = [c for c in missing if c not in remembered_caps]
            if still_missing:
                # Capability elevation is its own approval — granting it never
                # implies the execution is approved, and vice versa.
                return _PD(
                    False,
                    True,
                    "Sandbox capability required",
                    f"This action needs {', '.join(still_missing)} which the {intent.sandbox_profile} sandbox does not currently allow",
                    intent=intent,
                    approval_kind="capability",
                    remember_options=self._remember_options(intent,project_id,custom_agent_id),
                    required_capabilities=tuple(still_missing),
                    sandbox_profile=intent.sandbox_profile,
                )

        # 2. Remembered deny — beats every remembered allow at any scope.
        matched = self._matching_rules(intent, project_id=project_id)
        deny = [r for r in matched if r["effect"] == "deny"]
        if deny:
            rule = min(deny, key=_scope_rank)
            self._store.touch_policy_rule(rule["id"])
            return _PD(
                False,
                False,
                "Denied by remembered policy",
                f"{rule['display'] or intent.display} is denied in this scope",
                intent=intent,
                matched_rule_id=rule["id"],
                auto_resolved="deny",
                required_capabilities=intent.required_capabilities,
                sandbox_profile=intent.sandbox_profile,
            )

        # 3. Remembered allow — narrowest scope wins for audit display.
        allow = [r for r in matched if r["effect"] == "allow"]
        if allow:
            rule = min(allow, key=_scope_rank)
            self._store.touch_policy_rule(rule["id"])
            return _PD(
                True,
                False,
                "Remembered approval",
                f"Matches remembered {rule['scope_type']} rule",
                intent=intent,
                matched_rule_id=rule["id"],
                auto_resolved="allow",
                required_capabilities=intent.required_capabilities,
                sandbox_profile=intent.sandbox_profile,
            )

        # 4. Custom-agent approval mode — `autonomous` auto-approves only
        # sandbox-contained non-sensitive classes; it never conjures missing
        # capabilities (step 1 already handled those).
        mode = str((self._store.task_agent(getattr(ctx,"task",None) or {},custom_agent_id) or {}).get("approval_mode") or "standard")
        if mode == "autonomous" and intent.risk_class not in AUTONOMOUS_BLOCKED_RISKS and not missing:
            return _PD(
                True,
                False,
                "Autonomous approval",
                "Auto-approved by the custom Agent's autonomous mode inside its sandbox envelope",
                intent=intent,
                auto_resolved="autonomous",
                required_capabilities=intent.required_capabilities,
                sandbox_profile=intent.sandbox_profile,
            )

        # 5/6. Regex-policy defaults then ask — unchanged UX layer.
        if not base.approval_required:
            return _PD(
                base.allowed,
                False,
                base.reason,
                base.consequence,
                intent=intent,
                required_capabilities=intent.required_capabilities,
                sandbox_profile=intent.sandbox_profile,
            )
        return _PD(
            base.allowed,
            True,
            base.reason,
            base.consequence,
            intent=intent,
            required_capabilities=intent.required_capabilities,
            sandbox_profile=intent.sandbox_profile,
            remember_options=self._remember_options(intent, project_id, custom_agent_id),
        )

    # -- remembered-rule creation --------------------------------------------

    def record_resolution(
        self,
        intent: PolicyIntent,
        *,
        decision: str,
        remember: str | None,
        project_id: str,
        source_approval_id: str,
        approval_kind: str = "action",
    ) -> dict[str, Any] | None:
        """Persist the durable rule a resolution implies. Returns the rule or
        None for once/no-remember. Raises ValueError when the requested scope
        cannot apply (e.g. custom_agent scope on a task with no custom agent).
        """
        if decision not in {"approved", "denied"} or not remember or remember == "once":
            return None
        if remember not in REMEMBER_SCOPES:
            return None
        if approval_kind == "capability":
            # Capability rules may only name capabilities this profile's backend
            # can actually provide — a rule must never invent powers. The
            # intent's requirement list also carries baseline capabilities the
            # envelope always grants (process.execute, fs.workspace); those
            # are legitimate context but only the elevated subset is recorded.
            grantable = self._grantable(intent.sandbox_profile)
            envelope = self._envelope(intent.sandbox_profile)
            bad = [
                c
                for c in intent.required_capabilities
                if c in UNGRANTABLE_CAPABILITIES
                or (not _covers(grantable, c) and not _covers(envelope, c))
            ]
            if bad:
                raise ValueError(
                    f"capability rule cannot grant {', '.join(bad)} on profile {intent.sandbox_profile}"
                )
            capabilities = [
                c
                for c in intent.required_capabilities
                if _covers(grantable, c)
            ]
            if not capabilities:
                raise ValueError(
                    f"no grantable capability to remember on profile {intent.sandbox_profile}"
                )
        if intent.consent_binding.get("unbound"):
            raise ValueError("Managed remembered consent requires current originating authority")
        if intent.consent_binding and self._binding_lookup(intent.task_id) != intent.consent_binding:
            raise ValueError("Task authority changed; request fresh approval")
        scope_id = {
            "conversation": intent.consent_binding.get("conversation_id"),
            "task": intent.task_id,
            "project": project_id,
            "custom_agent": intent.custom_agent_id,
        }[remember]
        if not scope_id:
            raise ValueError(f"cannot remember for scope '{remember}': no such scope applies")
        effect = "allow" if decision == "approved" else "deny"
        action_type = "capability" if approval_kind == "capability" else "tool"
        return self._store.create_policy_rule(
            effect=effect,
            scope_type=remember,
            scope_id=scope_id,
            action_type=action_type,
            tool=intent.tool,
            fingerprint=intent.fingerprint,
            fingerprint_kind=intent.fingerprint_kind,
            matcher=intent.matcher,
            capabilities=(
                list(capabilities)
                if approval_kind == "capability"
                else list(intent.required_capabilities)
            ),
            sandbox_profile=intent.sandbox_profile,
            source_approval_id=source_approval_id,
            task_id=intent.task_id if remember == "task" else None,
            project_id=project_id or None,
            display=intent.display,
            consent_binding=intent.consent_binding,
            expires_at=time()+3600 if intent.consent_binding else None,
        )

    # -- internals -----------------------------------------------------------

    @staticmethod
    def _binding_matches(rule, binding):
        expected = rule.get('consent_binding') or {}
        if not expected: return True  # explicitly shared administrator/legacy host policy
        if not binding or binding.get('unbound'): return False
        if any(expected.get(key) != binding.get(key) for key in ('principal_id','policy_version')): return False
        if rule['scope_type'] == 'conversation':
            return all(expected.get(key) == binding.get(key) for key in ('conversation_id','session_id'))
        return True

    def _matching_rules(self, intent: PolicyIntent, *, project_id: str) -> list[dict[str, Any]]:
        scopes: list[tuple[str, str]] = []
        # Task-scoped rules only ever match inside the task that created them —
        # task_id isn't in scope for any other task, so they expire with it.
        if intent.task_id:
            scopes.append(("task", intent.task_id))
        if intent.custom_agent_id:
            scopes.append(("custom_agent", intent.custom_agent_id))
        if project_id:
            scopes.append(("project", project_id))
        if intent.consent_binding.get("conversation_id"):
            scopes.append(("conversation", intent.consent_binding["conversation_id"]))
        return [rule for rule in self._store.matching_policy_rules(
            fingerprint=intent.fingerprint, action_type="tool", scopes=scopes)
            if self._binding_matches(rule, intent.consent_binding) and (not rule.get("sandbox_profile") or rule["sandbox_profile"] == intent.sandbox_profile)]

    def _remembered_capabilities(
        self,
        profile: str,
        *,
        task_id: str | None,
        project_id: str,
        custom_agent_id: str | None,
        consent_binding: dict | None = None,
    ) -> set[str]:
        scopes: list[tuple[str, str]] = [("host", "")]
        if task_id:
            scopes.append(("task", task_id))
        if custom_agent_id:
            scopes.append(("custom_agent", custom_agent_id))
        if project_id:
            scopes.append(("project", project_id))
        binding = consent_binding if consent_binding is not None else self._binding_lookup(task_id or "")
        if binding.get("conversation_id"):
            scopes.append(("conversation",binding["conversation_id"]))
        remembered: set[str] = set()
        denied: set[str] = set()
        for rule in self._store.capability_rules(scopes=scopes):
            if not self._binding_matches(rule,binding) or rule.get("sandbox_profile") and rule["sandbox_profile"] != profile: continue
            if rule["effect"] == "allow": remembered.update(rule["capabilities"])
            else: denied.update(rule["capabilities"])
        remembered = {cap for cap in remembered if not _covers(frozenset(denied),cap)}
        # A remembered capability grant only counts where this profile's backend
        # can actually provide it — stored rules cannot invent powers the
        # runner does not advertise as grantable.
        grantable = self._grantable(profile)
        return {c for c in remembered if _covers(grantable, c)}

    def capability_grant_set(
        self,
        profile: str,
        *,
        task_id: str | None = None,
        project_id: str = "",
        custom_agent_id: str | None = None,
    ) -> frozenset[str]:
        """Capability grants a spawn under ``profile`` may realize right now:
        remembered allow-capability rules ∩ the backend's grantable set.
        Spawn-time mechanisms (e.g. linux-ns ``--share-net``) read this.
        """
        return frozenset(
            self._remembered_capabilities(
                profile,
                task_id=task_id,
                project_id=project_id,
                custom_agent_id=custom_agent_id,
            )
        )

    def grantable_for(self, profile: str) -> frozenset[str]:
        return self._grantable(profile)

    def grantable_covers(self, profile: str, capability: str) -> bool:
        return _covers(self._grantable(profile), capability)

    def _custom_agent(self, agent_id: str | None) -> dict[str, Any] | None:
        if not agent_id:
            return None
        try:
            return self._store.get_custom_agent(str(agent_id))
        except Exception:
            return None

    @staticmethod
    def _remember_options(
        intent: PolicyIntent, project_id: str, custom_agent_id: str | None
    ) -> tuple[str, ...]:
        if intent.consent_binding.get("unbound"): return ()
        options = ["task"]
        if intent.consent_binding.get("conversation_id"): options.append("conversation")
        if project_id:
            options.append("project")
        if custom_agent_id:
            options.append("custom_agent")
        return tuple(options)


def _scope_rank(rule: dict[str, Any]) -> int:
    return SCOPE_ORDER.get(str(rule.get("scope_type") or "host"), 3)
