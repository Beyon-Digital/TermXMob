# Chat component provenance

TermX uses **copied, locally adapted Vercel AI Elements source**, not a new hosted chat backend or an assumed shadcn core chat package. The existing `message.tsx`, `tool.tsx` and selected `prompt-input.tsx` exports already followed this model. This change extends that integration.

Official sources inspected on 2026-10-15:

- https://elements.ai-sdk.dev/ describes the shadcn-compatible source registry.
- https://ui.shadcn.com/docs/ai-elements now describes AI Elements as copied patterns composed from shadcn components; it explicitly permits existing copied components to keep working and provides a migration path. This is not a claim that an ordinary shadcn installation supplies every AI feature.
- https://github.com/vercel/ai-elements at `ca8be600bfd01b3131f9bf6134b51602a71f4374` supplied selected `conversation.tsx`, `attachments.tsx`, `confirmation.tsx`, `question.tsx` and textarea behavior. Its license is Apache-2.0; see `AI_ELEMENTS_LICENSE` and the per-file notices.

## Integration map

| Surface | Selected source / adaptation |
| --- | --- |
| Messages, streaming Markdown and fenced code | Existing Message/MessageContent/MessageResponse; Streamdown gets the active streaming state. |
| Conversation empty state | Upstream ConversationEmptyState. TermX retains its existing virtualizer, history and stable scroll-anchor restoration instead of replacing them with a second scroll controller. |
| Composer | Existing PromptInputBody/Tools/Submit plus selected PromptInputTextarea keyboard, paste and composition behavior. TermX owns durable drafts, retry IDs, queue/interrupt/steer behavior and file limits. IME Enter cannot invoke the external send handler. |
| Attachments | Upstream Attachments/Attachment/Preview/Info/Remove composition; callbacks update the existing per-session draft store. Optional hover-preview exports were not copied. |
| Tool calls | Existing upstream Tool/ToolHeader/ToolContent and TermX task-event adapter. |
| Approval | Upstream Confirmation/Request/Title surrounds the existing exact-operation review dialog. Host policy, scope, manual-operation gates and resolution endpoints are unchanged. |
| Clarification / supervision | Upstream Question/Input/Submit feeds the existing task-steering endpoint. No new autonomous answering or approval path is introduced. |
| Loading / failure | PromptInputSubmit states, streaming status, existing actionable retry/error paths and preserved pending prompts. |

Import paths, shadcn primitive adapters, class names and selected composition points are local. Unneeded optional dependencies and AI SDK transport hooks are not installed. React 19, existing shadcn/Radix primitives, AI SDK UI types and Streamdown are already dependencies of this workspace. All host transports, task state, authorization, history, virtualized rendering and durable draft handling remain TermX's existing implementation.

`ai-chat-elements.test.tsx` exercises IME safety, paste/remove adapters, approval state, attachment callbacks and clarification submission; the existing chat draft/performance/session-control tests remain applicable. These checks use fixtures and do not send paid provider traffic.
