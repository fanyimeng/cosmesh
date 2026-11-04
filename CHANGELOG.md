# Changelog

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
