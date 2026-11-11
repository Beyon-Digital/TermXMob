# Media studio

Choose **Studio** in the workspace switcher for a dedicated image, video and music creation space. **Agent** keeps chat, coding, review, terminals and the existing **Artifacts** document tools. Switching workspaces preserves the agent dock arrangement and visited editor drafts. The **Media studio & artifacts** command also opens Studio. The existing **Artifacts** tools stay in Agent; Studio keeps its own **Create**, **Music**, **Library**, **Saved assets** and **Settings** navigation. Music has its own song library and player; the visual Library shows images and videos.

## Source and layout

This integration adapts the current `src/` implementation from the sibling `vloop-studio` repository, rather than its older `packages/studio` React/Muapi package. It uses the current Higgsfield Cinema Studio interaction pattern: a large selected-output canvas, a compact bottom-centred creation dock, Image/Video switching, focused control panels, recent takes, camera prompt direction and a separate Library within a dedicated TermX workspace, using the existing authentication and light/dark themes. Both workspaces share a narrow app rail, quiet sidebar, neutral inset canvas and the same control and motion tokens; their editors remain independent. The camera and lens WebP previews in `desktop/workspace/public/assets/studio/cinema` are copied from that source. Cinema controls describe the aesthetics of a **still image**; they do not change a physical camera or guarantee an output pixel count.

The source-style `generateImage`, `generateVideo`, `processLipSync`, and `pollForResult` client methods use a shared dynamic facade in `desktop/workspace/src/lib/media-studio.ts`. The browser sends all studio requests to the TermX host. It never calls Muapi, OpenRouter, Kie, Netlify or Composio APIs directly and never receives provider keys. Provider-hosted Kie output links may still be previewed or opened by the browser.

The visual composer restores the earlier Higgsfield reference: side Image/Video rail, three Image or five Video option tiles above the prompt, then one compact model/output/settings/Generate footer. Billing notices and the extra generation review are removed. Keyboard focus uses contained inset indicators; static dialog/title focus targets do not draw large rings. [Current verification](implementation-status.md#verification) records the restored layout and exact final assets.

## Studio projects

Use **Studio → Projects → +** to create a named creative project, or **Manage projects** to edit its name and description, archive it, and restore archived projects. These collections are independent of Agent folder projects. Existing Agent projects remain selectable; Studio never renames, forgets or deletes their folders, conversations or worktrees. **No project** keeps an unassigned draft separate from a named collection.

Each Studio collection scopes generations, references, saved subjects and history to its owner. Project changes with unsaved prompts, references, lyrics or scene drafts ask you to **Keep editing** or **Discard drafts and switch**. Uploading, submitting or an uncertain unreceipted Music request blocks switching until it settles. Creating project metadata does not submit a generation.

**Archive project** is reversible removal from the active list. It requires the exact project name, preserves media/history/references, and rejects active or unresolved generation operations. Archived collections remain readable; restore them before generating or saving new content. A refresh cannot resolve a provider operation for which no receipt exists. Concurrent project edits reject stale revisions rather than overwriting another window.

The rail contains the single sidebar toggle. Project dialogs belong to that navigation layer, so their inputs and pending actions keep a floating/mobile sidebar mounted.

## Provider configuration

Open **Studio → Settings → Providers** to add or edit a compatible media account. The same saved accounts remain available in Agent’s **Settings → Models & engines**; connections are shared, while Studio model choices and limits are separate. Existing shared chat model selections and overrides are preserved by the Studio provider editor. OpenRouter uses `https://openrouter.ai/api/v1`; enable the required image, video or music capability in the Studio provider editor. Existing image/video-only accounts need **Allow music generation** enabled before Music can use them. Existing accounts are not changed automatically. The host uses its existing environment/OS credential store. **Generate** sends the current image/video request directly. **Create** does the same for Music. Editing prompts or settings never submits a generation. There is no billing checkbox or separate charge-confirmation dialog.

OpenRouter is the primary adapter. The host discovers its current image and video models from the dedicated public endpoints, caches metadata for five minutes, and populates supported aspect ratios, resolutions, durations and reference inputs. Unsupported vector and source-only operations are excluded. Image/Cinema composition accepts an ordered set of up to five image references when the selected model and provider endpoint support that count; Video, Lip sync and Remix retain their verified single-image reference contracts. A bounded discovery failure exposes a clearly labeled documented snapshot; it does not prove model availability or account entitlement.

Comparable source models are selected using actual OpenRouter identifiers, including Nano Banana Pro/2, Veo and other compatible image/video models. Portrait-plus-audio lip sync is exposed for the discovered `heygen/avatar-iv` model; supplied audio is distinct from script/TTS generation, and performance direction uses the model's motion-prompt option. Availability and billing remain subject to the chosen provider.

An explicitly configured Kie media adapter is also supported for a curated native model set. It is selected only through its own stored account. A failed or unsupported operation never silently switches accounts or providers. Additional providers require a documented server adapter and capability mapping; an arbitrary compatible chat URL is not assumed to support media generation.

## Studio settings and generation limits

**Models & defaults** selects the models available in Studio and optional defaults for Images/Cinema, Video, Lip sync, Remix and Music. Provider, model, aspect ratio, resolution and duration choices use the current supported catalog. Defaults prepare a new mode; they do not start generation or overwrite an existing manual selection, prompt or reference draft. Disabling a model is also enforced by the host before a new job is reserved. History and existing job reconciliation remain available.

**Generation limits** optionally bounds accepted Studio generations per UTC day, simultaneously active generations, and explicit video or music duration. Every limit starts off; zero prevents matching new generations. Settings belong to the signed-in principal and apply to their Studio jobs across projects on this host, including Factory scenes and Music. They do not cover Agent’s legacy artifact-generation endpoint or requests sent directly to a provider. Failed and unknown accepted attempts count toward the day; submitting, queued, running and unknown jobs occupy active slots. Rejected requests do not reserve a job. Replaying an existing request ID returns its receipt without using another slot. Limits are checked in the same SQLite write transaction that reserves the job, including concurrent windows.

A video-duration cap requires a supported, explicit duration within the cap. A provider default or reference-derived duration with no proven upper bound is rejected rather than treated as bounded. Daily limits reset at midnight UTC; the settings view displays the reset time. Configuration uses revision checks, so a stale window cannot silently overwrite newer settings. Reading or changing Studio settings requires host-level `ai-settings` authority; ordinary generation/read access does not grant configuration access.

These are local Studio request limits, not dollar budgets or a final bill estimate. OpenRouter documents provider-side key budgets, but changing them requires separate administrative management credentials; Studio does not request those credentials or change a provider-side budget. Use [OpenRouter key settings](https://openrouter.ai/keys) for that provider’s budget controls. The verified Kie documentation exposes account credit balance; this integration does not claim a verified per-key spend-limit API. Sources: [OpenRouter key management](https://openrouter.ai/docs/guides/overview/auth/management-api-keys), [OpenRouter limits](https://openrouter.ai/docs/api_reference/limits), [Kie account credits](https://docs.kie.ai/common-api/get-account-credits).

Saving settings, selecting defaults and configuring a provider never starts a generation. Submission remains an explicit Generate or Create action.

## Requests, history and output

The facade keeps the source request fields (`model`, `prompt`, `aspect_ratio`, `resolution`, `duration`, and optional image/audio/video reference URLs), adding the selected TermX provider, project and idempotency identity.

| Host endpoint | Purpose |
| --- | --- |
| `GET/POST /api/media/studio/projects` | List owned active/archived creative projects or create a collection |
| `PUT /api/media/studio/projects/{id}` | Update name/description using the current revision |
| `POST /api/media/studio/projects/{id}/archive` | Reversibly archive after active-operation and revision checks |
| `POST /api/media/studio/projects/{id}/restore` | Restore an owned archived collection using its current revision |
| `GET /api/media/studio/catalog?project_id=…` | Authorized enabled catalog, account metadata and Studio preferences |
| `GET/PUT /api/media/studio/settings` | Owned model defaults, enabled models, local limits and usage; host settings authority required |
| `GET /api/media/studio/pricing?project_id=…&model=…` | Published OpenRouter unit rates, with source and observation time |
| `POST /api/media/studio/jobs` | Reserve and start one explicitly requested image or video generation |
| `POST /api/media/studio/music/jobs` | Reserve and start one explicitly requested music generation |
| `GET /api/media/studio/jobs?project_id=…` | Latest active/polling window for this owner and exact project |
| `GET/POST /api/media/studio/subjects` | List or save owned project subject/product reference sets |
| `DELETE /api/media/studio/subjects/{id}` | Remove a reference set while retaining its image artifacts |
| `GET /api/media/studio/history?project_id=…&limit=40&cursor=…` | Paginated owned history, search, media kind and status filters |
| `POST /api/media/studio/jobs/{id}/refresh` | Reconcile an existing owned job |

Submissions return a durable receipt immediately while the host performs longer provider work in the background. Reusing an identical request ID returns the recorded job. Conflicting arguments are rejected. Interrupted or uncertain submissions remain visible and are never automatically created again. The browser retains separate opaque intent IDs per request digest, so unrelated work in another window cannot overwrite an uncertain request. Legacy pending IDs migrate to this format, and argument key ordering does not change identity. Late status responses cannot downgrade newer or terminal job states. Refreshing an existing remote job does not submit another generation. App shutdown cancels local transports while preserving this reconciliation state. The host revalidates the original owner and current project execution scope after catalog loading and immediately before provider submission; revocation before that effect records a failed receipt without calling the provider. Delayed catalog/status responses recheck read access before returning. Already-sent provider work cannot be undone by a later revocation.

OpenRouter image bytes and authenticated video content are validated, bounded and stored as owned, project-scoped artifacts. The client loads previews only when their cards enter the visible scroll area, through authenticated same-origin transfers. The generation gallery and saved-photo list each allow up to three concurrent authenticated local-preview transfers; leaving the viewport, hiding, locking, or changing owner aborts pending transfers and revokes temporary object URLs. Open and Download remain explicit actions even before a preview loads. Kie outputs remain provider-hosted links; they may expire and should be downloaded promptly. History records do not promise indefinite availability of remote media.

Polling runs only while the studio is visible and the browser is foregrounded, with bounded batches and a five-minute automatic window. Manual refresh remains available. Owner/project changes and workspace locking fence late responses, clear sensitive previews and stop playback.

## Advanced source tools

The Video tool switch offers generation, **Remix video**, and **Content factory**. Remix appears with models whose actual OpenRouter catalog and provider endpoint contracts declare the required video inputs and output. Source clips are passed as explicit public HTTPS video references; models without a verified transport are not treated as equivalents merely because their names suggest editing. Supported reference roles and negative prompts come from verified capabilities.

Image and video results offer contextual **Regenerate**, **Edit**, **Animate** and **Use as reference** actions when the selected model supports them. These prepare a draft and never start paid generation. The creation dock uses compact top tabs and inline parameters, with further controls disclosed on demand.

The content factory combines a brief, platform and visual style with up to six distinct shot templates. Every scene prompt is editable. Drafting is local and does not claim AI writing or consume model usage. Queuing requires approval for the remaining scene count and selected account/model. Every scene has a separate durable job; a timeout, lock, hidden view, owner/project change or uncertain receipt pauses further queuing. A paused scene is never automatically resubmitted.

The Library now searches the complete owned project history using stable cursor pagination. New inserts do not drift into an existing page sequence, and tied timestamps have a deterministic order. Search and kind/status filters are applied by the server. In-progress and needs-attention filters include their complete status groups. **Load more generations** retrieves older pages; foreground status refreshes retain previously loaded history.

**Reuse settings** loads the saved provider/model, prompt and supported controls without starting a generation. **Edit image** and **Animate** prepare a new draft from an owned image output or an explicitly public provider URL. Billing consent must be given again. Temporary query-bearing source URLs are not persisted in reusable settings; the UI asks for them again.

Where the selected OpenRouter model declares inline image support, **Choose image** uploads a static PNG/JPEG/WebP to this workspace and uses its owned artifact ID. The host verifies owner/project, binary type, at most 8 MiB, at most 32 million pixels and image validity before looking up provider credentials or reserving a generation. For Image/Cinema, add up to five local or public HTTPS image references, reorder them, or select a complete saved set. The ordered `image_references` array contains `{image_id}` or `{image_url}` objects and cannot be combined with the legacy single-image fields. The host bounds aggregate encoded local input to 20 MiB. Before every OpenRouter image submission it checks the current dedicated image endpoint metadata for the exact count, resolution and aspect ratio, then pins an eligible provider with fallback disabled. A model-wide capability union is not enough: a provider lacking the selected resolution is excluded. Local image bytes are embedded only at the supported provider request boundary; no provider key or inline image is stored in job history. Safe reuse preserves reference order and asks for any omitted temporary signed URL in its original slot. Audio/video references remain public HTTPS URLs because local audio/video data-URI generation contracts have not been established.

**Saved subjects & products** stores a name, description and one to five owned image references in this project. Upload and save a set, search it, and choose an individual portrait/product image or the complete ordered set for Image/Cinema. Sets exceeding the selected model limit are disabled with an explanation; references are never silently truncated. Video tools use individual image selection. Removing a set deletes its metadata and keeps the uploaded artifacts. This is a reusable reference library, not model training or an identity-preservation guarantee.

Advanced operation support is checked against actual OpenRouter catalog modalities and provider endpoint metadata. Lip sync and Remix require documented request mappings plus matching provider architecture and passthrough fields; the adapter pins that verified provider. The checked public metadata and source URLs are retained in [capability evidence](implementation-status.md#media-studio) and the raw test fixture. No model name is treated as sufficient proof of input/output support.

## Music creation

**Studio → Music** adapts the inspected Suno Create workflow: a creation column alongside **My songs**, **Simple / Advanced** modes, disclosed Lyrics/Styles/More options and a persistent player for the selected owned track. The creation column and library scroll independently. TermX keeps its own branding, accessible controls and shared app frame; it does not copy account content or advertise unimplemented social actions. [Reference notes](implementation-status.md#design-references) describe the actual browser inspection.

Music is selected by supported operation and compatible saved account. The OpenRouter music catalog reads the public `/api/v1/models?output_modalities=audio` inventory, intersects it with implemented music contracts and excludes speech-only audio models. The supported routes are:

- **OpenRouter → Lyria 3 Pro / Lyria 3 Clip.** Use the existing OpenRouter account with **Allow music generation** enabled. Pro accepts a duration target (3–184 seconds); this is prompt guidance, not an exact-length contract. Clip produces 30-second music. Description, styles, lyrics and instrumental directions are preserved in the music prompt; their adherence is model-controlled. Output uses OpenRouter's documented streamed Chat Completions audio interface. Public catalog discovery has a separate cache from visual models; a valid live inventory removing a model removes its availability. A failed public discovery uses the documented snapshot, exposed through `music_catalog_source`.
- **ElevenLabs → Eleven Music 2.5.** Use `https://api.elevenlabs.io` with Audio capability. Simple description/instrumental generation supports 3–600 seconds. Advanced lyrics use the documented composition plan, with a 3–120 second total, at least 3 seconds per section, and provider section/line constraints. These constraints apply to ElevenLabs rather than every music model.

Provider keys stay in the host credential store. No account is created automatically. The Simple / Advanced editor and model-specific controls preserve their existing layout. Switching between models adjusts an unsupported duration while retaining the draft's lyrics and styles; unsupported input is rejected before submission. Longer ElevenLabs custom compositions, voice personas, uploaded inspiration, stems, covers, mashups, Weirdness/Style Influence and track editing remain unsupported.

**Create** validates and submits the current Music draft as one durable attempt. Unconfigured, hidden, locked, archived or uncertain drafts cannot submit. A timeout or interrupted provider stream remains **Unknown**, with no automatic duplicate submission. Refresh reconciles recorded state. Neither implemented synchronous audio stream has a remote task polling contract to recover interrupted bytes. OpenRouter music has no active-generation deadline by default; an explicitly configured provider request deadline is honored. A new attempt requires another explicit generation action.

Music shares Studio's accepted-attempt daily and active-job limits and adds an optional **Music duration** cap. These caps apply atomically before provider submission. A zero duration cap prevents new songs. For prompt-controlled duration, admission compares the model maximum with an enabled music-duration cap rather than pretending the requested target is a hard ceiling. Old Studio settings without a music cap retain their previous behavior. Music model defaults and disabled-model checks use the same Studio Settings revision controls.

Completed MP3 or supported PCM WAV bytes are bounded, structurally checked and stored as an owned project artifact. OpenRouter requires its final successful stream completion; truncated or malformed audio is never presented as a finished song. Playback and download use authenticated same-origin artifact transfers. Hiding or locking Music, changing its owner/project or leaving the app stops playback, aborts pending transfers and revokes temporary object URLs. Track titles, styles and receipts come from the owned job history; an empty library contains no fabricated tracks.

[Suno's official API platform](https://platform.suno.com/) exists, but its documentation and access contract were gated during this work. This integration does not use browser credentials, third-party Suno wrappers or claim that ElevenLabs produces Suno model output. A Suno adapter requires a verified documented contract and a separately configured eligible account. Implemented contracts include [OpenRouter audio output](https://openrouter.ai/docs/guides/overview/multimodal/audio), [Lyria 3 Pro](https://openrouter.ai/google/lyria-3-pro-preview), [Lyria 3 Clip](https://openrouter.ai/google/lyria-3-clip-preview), [ElevenLabs Compose music](https://elevenlabs.io/docs/api-reference/music/compose), [composition plans](https://elevenlabs.io/docs/eleven-api/guides/how-to/music/composition-plans) and [API availability](https://elevenlabs.io/docs/eleven-creative/products/music).

## Published rates

Expand **Published rates** in the creation controls or Factory to fetch the selected model's public pricing metadata. Image rates come from dedicated image provider endpoints; video rates use the video catalog's declared SKUs. The guide preserves USD values and units such as image tokens, video tokens or seconds, alongside provider/variant, source and observation time. It does not turn token rates into per-image totals or use general chat zero-token placeholders. Unknown pricing fields are omitted explicitly. There is no generation quote or total estimate.

Metadata fetches require the current workspace scope, use no provider credentials, are bounded to four concurrent requests and four seconds including queuing, and cache successful results for five minutes. Failures remain retryable. The panel stays collapsed by default and performs no pricing request until opened. Published rates do not prove account entitlement or available credits.

## Remaining migration limits

Multi-image video arrays, training or voice cloning, source-specific motion transfer/automatic character swap, and provider total-price estimates are not implemented. They require their own verified transport or additional product work. Local audio/video uploads into generation remain unsupported. Existing artifact upload/conversion tools remain available in **Artifacts & documents**. No production provider credentials are created by this integration, and automated generation tests use mocks.

## Verification

Use the repository's locked media/development extras and a compatible Node runtime (CI uses Node 22; the locked `nodejs-wheel` runtime supplies Node 24 on this Mac). The Mac's global Node 26 exposes browser-storage behavior incompatible with this project's current jsdom tests.

```sh
uv sync --locked --extra media --extra development --group dev
# On this Mac, prefer the locked Node 24 binary over global Node 26.
export PATH="$PWD/.venv/lib/python3.14/site-packages/nodejs_wheel/bin:$PATH"
pnpm --dir desktop/workspace exec vitest run
pnpm --dir desktop/workspace build
uv run --no-sync pytest tests/test_media_studio_settings.py tests/test_media_studio.py tests/test_media_studio_openrouter.py tests/test_media_studio_pricing.py tests/test_media_studio_subjects.py tests/test_media_workspace.py tests/test_provider_declarations.py tests/test_authorization.py tests/test_access_renewal.py
uv run --no-sync python tests/workspace_studio_e2e.py /tmp/termx-studio-ui
uv run --no-sync python tests/workspace_studio_advanced_e2e.py /tmp/termx-studio-advanced-ui
TERMX_STUDIO_FOCUS=composition uv run --no-sync python tests/workspace_studio_advanced_e2e.py /tmp/termx-studio-composition-ui
TERMX_STUDIO_FOCUS=lazy uv run --no-sync python tests/workspace_studio_advanced_e2e.py /tmp/termx-studio-lazy-ui
```

The browser script uses an isolated real host, managed sign-in, genuine artifact exports, and explicitly mocked provider jobs. It covers desktop/mobile, both themes, generation states, reference/settings payloads, previews, artifact switching and accessibility. Public model discovery is nonbillable research; it does not verify a paid image, video or lip-sync generation. Native debug builds, Rust tests and controlled own-WKWebView rendering/IPC checks are recorded separately from browser proof. That instrumentation is test-only and its patch is retained. It reproduced and verified a narrowly scoped notification-status ACL fix in the main and updater windows; it requests no OS notification permission. Authenticated native media flows, signed packaging, installed-app behavior, and live provider output require separate qualification. See [verification evidence](implementation-status.md#verification) for exact totals and boundaries.

The dedicated Studio Settings change passed all 1,481 frontend tests, TypeScript checking and the production build, plus 316 Studio backend tests and clean targeted backend type checks. Its final isolated-host browser matrix passed 20 functional/viewport states, including genuine scratch-provider configuration, settings persistence/conflicts, model defaults, disabled models, local-reference/Agent draft preservation and pre-dispatch quota refusals. Two axe color-contrast checks encountered an internal nested-scroll geometry error; the full metadata and separate visible-model checks are retained rather than reported as complete passes. See [Studio Settings verification](implementation-status.md#verification). The exact tested frontend was applied locally; the idle supervised backend was gracefully restarted with a fresh SQLite backup, preserving host identity, both existing provider accounts and profile configuration. No provider or generation-limit values were set in the real profile, and no paid generation was performed.

Primary adapter documentation checked during implementation:

- [OpenRouter Image API](https://openrouter.ai/docs/guides/overview/multimodal/image-generation)
- [OpenRouter Video API](https://openrouter.ai/docs/guides/overview/multimodal/video-generation)
- [HeyGen Avatar IV](https://openrouter.ai/heygen/avatar-iv)
- [Kie task status](https://docs.kie.ai/market/common/get-task-detail)
