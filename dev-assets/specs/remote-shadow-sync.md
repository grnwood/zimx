# Remote Shadow Sync (UI)

## Goal
Enable a local vault to asynchronously shadow all write operations to a configured remote vault. Local saves must never block. Remote sync must handle offline periods, attachments, deletes, renames, and folder moves. Conflicts are resolved after the fact in a diff viewer.

## Non-Goals
- Real-time collaborative editing.
- Full remote read validation or polling on every read.
- Automatic conflict merges without user input.

## User Story
Two PCs have local copies of the same vault. Each is configured to shadow writes to the same remote vault. Every local write is queued and sent to the remote server in the background. If the remote write conflicts, the user resolves it in a diff viewer without blocking local saves.

## UX Summary
- Preferences: add a “Remote Shadow Sync” section with enable toggle, server URL, username, password, remember token, verify TLS.
- Status indicator: “Shadow Sync: Connected / Offline / Syncing / Needs Attention”.
- Conflicts: show a diff dialog with actions: Keep Local (force write), Keep Remote, Merge (manual), Retry.
- Offline: queue continues accumulating; status shows offline/backlog.

## Requirements
- Asynchronous, non-blocking; local writes complete immediately.
- Persistent queue of pending sync operations.
- Coalesce repeated writes for the same path (keep newest content).
- Support file writes, attachment uploads, deletes, renames, folder moves, and reorders.
- Use server-side conflict detection (If-Match) and show diff on 409.
- Works when remote API is intermittently unavailable.

## Data Model
### Local Queue (persistent)
Store in local settings DB or a small SQLite file under local vault metadata (remote cache root if remote mode is used elsewhere).

Fields (suggested):
- id (uuid)
- op_type: write | delete | rename | move | reorder | attach
- path
- payload (json or bytes reference)
- base_rev or base_mtime (known from last successful remote write)
- created_at, updated_at
- attempts
- last_error
- state: queued | inflight | conflict | failed

Notes:
- For `write`, payload includes content.
- For `attach`, payload includes list of file paths and page path.
- For `rename/move`, payload includes from/to.
- For `delete`, payload includes path.
- For `reorder`, payload includes parent_path + order list.

### Revision Tracking
Store last known remote rev/mtime per path to send `If-Match` on writes. This can live in the same DB table or a separate kv store.

## API Usage
- Writes: `POST /api/file/write` with `If-Match: rev:<n>` or `If-Match: mtime:<ns>` if available.
- Attachments: `POST /files/attach` (existing endpoint used by import).
- Deletes: `POST /api/file/delete` (existing endpoint) with version/If-Match if supported.
- Moves/Renames: `POST /api/file/move` (existing endpoint) with version/If-Match if supported.
- Reorders: `POST /api/file/reorder`.
- Conflict response: HTTP 409 returns `current_content` (already supported by server) for diff.

## Flow
1. Local save completes immediately.
2. Sync layer enqueues an operation (coalesced if same path and op_type).
3. Background worker attempts sync:
   - If remote reachable and authenticated, send op with If-Match.
   - On success, update last known rev/mtime, mark op done.
   - On 401, prompt for login and retry later.
   - On 409, mark conflict and surface UI dialog (non-blocking).
   - On network error, backoff and retry.

## Conflict Handling
- On 409, display diff viewer using:
  - Local version (latest saved content)
  - Remote version (server’s `current_content`)
- Actions:
  - Keep Local: force write without If-Match (or using updated base after user confirmation).
  - Keep Remote: discard local shadow op; optionally pull remote content to show.
  - Merge: user edits merge and writes merged content, then enqueues new write.

## Offline and Backlog
- Queue persists across restarts.
- Exponential backoff on failures, with a max retry interval.
- Status UI shows backlog size and last error.

## Coalescing Rules
- For multiple writes to same file: keep only latest content.
- For delete after write: drop the write, keep delete.
- For rename/move: update any queued entries for the old path.
- For attachments: keep latest attachment set or append, but avoid re-uploading files already uploaded.

## Authentication
Reuse existing remote auth flow:
- Store refresh token in global config (`remote_auth`).
- Sync worker uses the same token refresh logic as UI API client.

## Edge Cases
- Attachments referenced by a page write must be uploaded before or with the page write.
- Rename/move of a folder should rewrite queued paths beneath it.
- Conflicts in rename/move should surface as separate conflict items.
- Large file uploads should be chunked or retried; if chunking is not supported, retry whole upload.

## Telemetry/Logging
- Log sync attempts, errors, and conflict events to a local log.
- Provide a debug toggle for detailed sync traces.

## Open Questions
- Should “Keep Local” force overwrite remote without If-Match, or rebase and require explicit confirmation each time?
- Should we allow a manual “Pull remote state” action for a file after conflicts?
- Do we need a “pause syncing” toggle per vault?

## Implementation Notes
- Build a `ShadowSyncManager` in `zimx/app/` or `zimx/app/ui/` with:
  - enqueue(op)
  - run_worker()
  - resolve_conflict(op_id, resolution)
- Hook into all write paths in `MainWindow`:
  - `_save_current_file`, delete/move/reorder handlers, attachment upload paths.
- Use existing `httpx.Client` configured for remote auth.

