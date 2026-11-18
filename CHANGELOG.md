# Changelog

## 0.4.7

Released 2026-11-18. Includes the previously unreleased 0.4.4–0.4.6 changes.

- Deliver pinned records from the sender’s own Thread, with recipient ACL checks, idempotent retries, offline drafts and explicit per-recipient read marks. Preserve legacy message IDs and historical read state.
- File new incoming message references into ordinary private person Threads; preserve the same ID on another device, reuse stable identity bindings and require explicit choice on name collisions.
- Add permission-filtered full-text Thread/message indexes with per-record refresh, fixed-source retrieval and pinned version-to-version change lists.
- Set decimal defaults to personal 5 GB/file, 10 GB/batch, 30 GB/account; outward 2 GB/file, 5 GB/batch, 15 GB/Thread including history. Preserve explicit custom policies and accepted data.
- Retire old multi-author sending entrances, align active manuals and managed Skill, retain historical formats and reversible software upgrades.

## 0.4.6

Unreleased candidate. Production unchanged.

- Migrate accepted legacy public Threads offline with the same IDs, creator ownership and complete available history; preserve original repositories, private streams and an independent backup. Interrupted migration stays closed and resumes.
- Fence migrated Hubs against older runtimes. Retire public Git, legacy online message/page endpoints and CLI share; keep old data for offline review and rollback.
- Inherit current source Thread access across all Library versions, catalogs, search, extracted text and files. Require explicit authorization for independent publication and prove upload ownership by received content.
- Raise private transfer defaults to 20 GiB per file / 100 GiB per batch and published defaults to 10 GiB / 50 GiB, with 100 GiB current and 1 TiB retained Thread capacity. Allow opaque science data, archives, binaries and unknown suffixes while retaining credential/path checks. Stream large Library files and skip automatic extraction above 20 MiB.
- Replace obsolete managed guidance, keep previous packages and make migration/rollback boundaries explicit.

## 0.4.5

Unreleased candidate; includes 0.4.4. Production remains unchanged.

- Add single-owner private/restricted/public Thread snapshots with authenticated online access to records, history and explicitly selected files. Readers never gain write or delegation rights.
- Suggest a private ordinary person-tracking Thread for incoming restricted work; reuse existing Threads, retain personal notes, follow stable user IDs, exclude all-user publications, and recheck access on each view/download chunk.
- Enforce file, transfer and retained Thread quotas. Keep private execution coordinates out of online responses and preserve current accepted content on failure.
- Isolate access-managed content from legacy public Git and Library; block legacy-ID narrowing and protected-ID Git publication pending explicit migration. Legacy Library promotion is deferred.
- Preserve schema 2, personal synchronization, installed software rollback and original materials. No production deployment or automatic creation of user Threads.

## 0.4.4

Unreleased candidate. Production remains 0.4.3.

- Add owner-only synchronization and recovery of a selected Thread with the same ID, full available document history and per-device project/directory associations.
- Restore into existing local data without replacing its identity or other Threads; transfer only selected Thread assets and fetch files on demand.
- Restore explicitly selected files into portable relative paths without overwriting local work; report missing files, conflicts and skipped large data, and sync subsequent edits with stale-copy protection.
- Remove the whole-store client implementation; retire its commands and asset push with migration guidance. Keep the server compatibility protocol, existing snapshots and previous software for older clients and rollback.
- Preserve authenticated public authorship when publishing a restored Thread; isolate each participant's private execution locations and reject ownership inferred from a colliding local username.
- Sync an owner's execution locations for a public Thread across devices under the same ID. Fetch public facts from their existing repository, keep private bindings in a separate owner-only relay stream, and preserve prior private snapshots when the Thread becomes public.
- Replace managed Skill/help on upgrade and show a prominent migration notice; original documents, configuration and previous software remain intact.

## 0.4.3

- Add a personal-client default Hub setting, separate from the software update source and Hub administration.
- Preserve existing workbench destinations and explicit connection overrides; clarify server-hosted DSH as an ordinary personal client.

## 0.4.2

Released 2026-11-16. Includes the previously unreleased 0.4.1 personal-sharing work.

- Preserve the 0.4.1 personal-sharing candidate and add a separate reversible compatibility fix.
- Empty optional record bodies now receive the existing nonempty fallback title, restoring state/archive/restore commands without changing stored document schemas or explicit titles.
- Add opt-in synthetic shared-home probes for cross-host locks, concurrent writes, crash recovery, local caches and authoritative relay isolation.
- Show a prominent personal-sharing guide in the upgrade receipt; sync remains opt-in and public sharing retains its existing authorization rules.

## 0.4.1 (unreleased candidate)

- Opt-in personal workbench replicas with owner-only gateway storage, fixed-source preservation, conflict checks and recoverable local application.
- Unlimited same-device file references; private streamed file relay with resumable chunks, quotas, content filtering and on-demand verified downloads.
- Additive private sidecars preserve schema 2 and ordinary 0.4.0 workbench operation. Public attachment rules remain unchanged. No background transfer or production migration.

## 0.4.0

Released 2026-11-10.

- Automatically show the important-update guide after installation or an upgrade, including through legacy client receipts.

- One CosMesh Skill for private and public Threads; retained legacy CLI/data compatibility.
- Prominent not-sent warning for private work; record-specific outbound acceptance, scoped publication and retry.
- Optional selected-text public branches with private provenance, shown only on request.
- Transactional retirement/backups of the managed CosBase Skill and rollback restoration.
- Independent complete entry titles; concise checks with optional background/details.

## 0.3.28

- Accept UTF-8 text and source attachments by content, including LaTeX, scripts, configuration files and extensionless text.
- Apply the same checks during attachment selection, library import and Hub acceptance, with explicit skip reasons for data, archives, binary programs and invalid text.
- Extract source text for search and preserve fixed-reference opening, existing document formats and attachment size limits.

## 0.3.27

- Capture Agent-authored project increments in ordinary Thread records, deduplicate exact repeats, retain private checkpoints and publish only the selected shared update.
- Add prepared private inbox/outbox messages for request interactions and corrections, with explicit per-user seen state, frozen pagination and historical initialization.
- Preserve the request-inbox JSON contract; ordinary project progress does not notify others or upload attachments.
- Update both Skills with project capture, message routing and on-demand source reading.

## 0.3.26

- Prepare older or incomplete derived caches on the first read after an upgrade; subsequent overviews use the finished product without manual refresh.
- Retain valid previous products while normal content updates are prepared in the background.

## 0.3.25

- Prepare cached products after pulled or accepted content changes, while keeping request-time reads free of extra Git or workbench loading.
- Retain the previous client's derived-index compatibility and distinguish interaction-summary previews from full original bodies.

- Store submitting Agents' concise interaction summaries in ordinary versioned records, and read the same conversations from Skills, chat transcripts and a left/right terminal view.
- Offer cached request/Thread conversations, indexed pagination, explicit refresh and on-demand full-record reading without a server model or an extra message store.
- Answer routine request/progress questions with one prepared check containing the Thread introduction, latest recipient response and metadata-only material status.
- Read warm checks from the existing MD/SQLite cache without loading the workbench or downloading originals; retain separate reply and formal delivery states.
- Offer compact inbox and write receipts, reuse the incoming-reference index for request states, and make both Skills stop once the current question is answered.
- Keep file opening, checksum verification, detailed source reading and execution on demand, with original interfaces and private boundaries preserved.

## 0.3.23

- Share text briefs by default; upload related documents only when explicitly selected, through the versioned knowledge library before publishing their references.
- Resolve fixed knowledge-file references into checksum-verified local files, with explicit system-viewer opening.
- Default to skipping data/archive suffixes; enforce 20 MiB per file and 100 MiB per share, and label incomplete materials.
- Preserve private originals and execution locations, existing versions and unrelated knowledge drafts; reuse unchanged materials and pin code to Git commits.

## 0.3.22

- Default to switchable fast interaction, preserving private preferences across upgrades.
- Bind Threads directly to agent projects/workspaces across Codex, DSH and OpenCode; show association status in one overview table.
- Save bilingual short names and introductions as ordinary authored records, retaining historical aliases and stable IDs.
- Treat explicit project onboarding as authorization to create or reuse a private Thread; clarify only material ambiguity.

- Return ready-to-display status documents with a compact, deduplicated source index; retain full structured pages for detailed reads.
- Reuse prepared overviews and append only the selected work's additional status, keeping overall introductions and privacy markers.
- Make both Skills present existing documents directly and reuse current evidence and successful receipts when sufficient.

## 0.3.21 (development candidate)

- Prepare Thread and user status pages with an overall introduction at every level, status summaries and paginated source indexes.
- Read local Markdown/SQLite products first; coalesce changes into bounded background updates without a model or network polling.
- Preserve source records, authors, corrections and private execution boundaries; retain the existing detailed read interfaces.

## 0.3.20

- Combine a compact private overview and an explicitly selected Thread continuation in one local read.
- Share pinned sources and canonical text between the views while preserving complete continuation evidence.
- Route combined requests through both Skills without a second Agent tool round trip; retain separate reads and Mesh privacy boundaries.

## 0.3.19

- Keep private paths out of shared-overview failure diagnostics while preserving error codes.
- Publish the compact overview and thin Skill workflow validated in the 0.3.18 candidate.

## 0.3.18

- Make both Skills use one local compact overview, with target-specific host checks when continuing work.
- Deduplicate pinned sources and common explanations, bound progress excerpts and retain private execution evidence.
- Preserve the detailed overview contract and exclude private bindings from shared compact views.

## 0.3.17

- Add a read-only Thread overview with progress, private Agent/session/directory bindings and optional supported-host project metadata.
- Label verified, registered and missing execution evidence; keep directory project matches as candidates.
- Make both Skills proactively show compact overviews; exclude all private execution metadata from Mesh overview responses.

## 0.3.16

- Upgrade pip, setuptools and wheel explicitly in GitHub cold-start prompts; diagnose missing project-wheel metadata with a recovery command.

## 0.3.15

- Generate copyable, versioned installation prompts for server and GitHub/private-Base onboarding without exporting inviter data.
- Preview CV/intro imports with the existing document extractor, Agent-proposed fields and explicit Profile version checks.
- Add interactive Profile filling with preview, confirmation and preservation of unanswered fields.

## 0.3.14

- Support DeepSeek Harness (`dsh`) in Skill installation, diagnostics and capability discovery.
- Detect the bundled macOS DSH launcher when it is not on PATH.
- Share the existing two Skills and CLI with DSH, with Full access guidance for work across directories.

## 0.3.13

- Launch remote Git transport, managed commands and receive hooks through Python to avoid shared-home script execution failures.
- Prepare clones in temporary directories, and preserve older interrupted Mesh clones before retrying.
- Verify upgrading the previous local-first client against the live shared-home deployment.

## 0.3.12

- Publish the Thread-based CosMesh / CosBase release for macOS and Linux.
- Keep custom Hub discovery paths through portable installation and updates.
- Preserve lifecycle compatibility metadata in portable installations and reuse verified wheels.
- Include maintenance requests, unified legacy search, knowledge backlinks and personal Agent coordination.

## 0.3.11

Unreleased maintenance Thread candidate.

- Discover a registered maintenance Thread and submit ordinary requests to its creator.
- Reuse replies, assignments, results, delivery and requester adoption for system feedback.
- Guide new feedback through Threads; retain legacy feedback history and pending queues.
- Remove the retired experimental Mesh Agent, its controls and deployment options.

## 0.3.10

Initial public source preview.

- Unified search across Threads, records, knowledge, and read-only legacy workspaces.
- Agent-led query expansion with bounded source reading and version-aware backlinks.
- Thread continuation across sessions, splitting, merging, recycling, and withdrawal.
- Versioned knowledge documents, named user directory, and contribution provenance.
- Local-first CLI and standard Skills for Codex and OpenCode.
- Explicit Git-over-SSH synchronization using existing server identities.

Local development notes and deployment histories are maintained separately.
