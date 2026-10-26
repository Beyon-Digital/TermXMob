# Chat-first interaction model, 25 October 2026

The desktop/browser renderer lives in `desktop/workspace`; the separate `termx-app` repository is the Expo remote controller. This implementation is based on `termxmob` main `fb8ae4b` plus the 101 existing local source changes preserved as local snapshot `745fc7d`. The original repository and its other worktrees have not been edited. Work is isolated on `codex/chat-first-orchestrator`.

## Evidence and product decision

The before browser walk used an immutable copy of the original production bundle, actual host/auth/session APIs and synthetic state. Root opened the control plane, creation foregrounded project/preset/folder fields, a selected chat briefly displayed a contradictory create-a-conversation message, and Code led to an empty workbench without a project. Draft restoration and narrow navigation already worked and are retained. Before screenshots/report are under `output/playwright/out/sessions/2026-10-25/baseline-201400`.

User direction is one persistent coordinating agent conversation, with specialists directly inspectable and interactive on demand. The primary conversation is the home destination. New chats are focused secondary conversations; recent activity is the default arrangement and project grouping remains optional. Projects and operations remain available in the sidebar footer; code, browser, terminal, changes, computer and artifacts are contextual workspace actions. Existing links, engine sessions, panel mounting, shortcuts and settings remain supported.

The official [Grok Bot overview](https://docs.x.ai/grok-bot/overview), [Bot roles](https://docs.x.ai/grok-bot/bots) and [collaboration guide](https://docs.x.ai/grok-bot/chat-and-collaboration) describe persistent named teammates, messaging, coordination, ownership and visible updates. They inform interaction requirements, not a claim about Grok's internal schema. The repository's existing [Codex references](../workspace-ui.md) document previously reviewed official demos. The current supported CUA inventory has no enabled native apps/browser surfaces, so this run cannot claim installed Codex or native TermX parity. Current official app documentation was read as supplementary context.

## Visual system

Retain TermX's green signal accent and Geist type. Neutral graphite canvas `#1c1e20`, navigation `#17191b`, surface `#242729`, foreground `#e8e9e9`, secondary `#afb4b7`, green signal `#a5d6b4`; equivalent light tokens are supplied. The only new brand mark is the small three-bar agent signal beside Main agent. Body text stays readable; controls use sentence case and visibly explain failures.

A persistent sidebar landmark identifies Main agent, with recent chats beneath. The pinned sidebar removes the redundant icon rail; collapsing it exposes the rail again. A quiet header names the conversation; secondary surfaces open only when requested. Delegated work appears as an expandable status area above the transcript. It displays child state/results/errors and routes into exact chats. Direct control is an explicit revision-bound handoff; opening a child alone does not transfer ownership.

## Functional repair areas

- Failed sends retain their idempotency key through panel replacement, window journal recovery and redock validation.
- Pending Stop requests are guarded across panels and expose progress; queued follow-ups remain distinct.
- Missing connections offer a direct route to account setup while preserving the draft.
- Provider setup guides common destinations and hides technical declarations under Advanced. Test connection explicitly reviews an inference request, destination/model and possible charge before dispatch.
- Fresh unauthenticated browsers do not attempt a CSRF-protected refresh without the required session marker.
- Primary-agent lookup/creation is single-flight, owner fenced, and cannot override later chat/settings navigation. Browser Back and unlock retry restore the home intent.

Validation and exact remaining runtime/adapter limits are recorded in the final verification report. No external model calls, paid provider, GitHub CI, push, deployment, or local Rust compilation are part of this change.
