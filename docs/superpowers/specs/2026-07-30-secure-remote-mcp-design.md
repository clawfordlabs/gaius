# Secure Remote MCP Design

**Date:** 2026-07-30
**Status:** Approved

## Purpose

Gaius currently exposes a trusted local stdio MCP server. Hosted agent surfaces
need remote access, but the local tool surface assumes its caller already has the
same filesystem and process privileges as the user.

This design adds an explicit remote MCP profile for personal, authenticated
connections. The first deployment uses an outbound-only OpenAI Secure MCP
Tunnel. The profile is generic and remains useful behind a future HTTPS/OAuth
gateway for other hosted agent surfaces.

Remote access is opt-in. A default Gaius installation remains local-only and
sends no data off the user's machines.

## Scope

The first remote profile supports:

- searching the synced Gaius memory store;
- listing projects;
- reading a complete memory document;
- reading project state and recent decisions;
- adding a memory;
- recording a project handoff;
- logging a decision; and
- automatic Git synchronization around every operation.

It does not support:

- `run_task` or task status;
- external roots;
- arbitrary filesystem access;
- anonymous or public access;
- artifact download URLs;
- a native HTTP transport or OAuth server; or
- public plugin distribution.

## Architecture

```text
Hosted agent surface
        |
Authenticated outbound tunnel
        |
tunnel client
        |
gaius-mcp --profile remote
        |
dedicated memory checkout and derived index
        |
git-only transport
        |
canonical memory Git repository
```

The tunnel client and Gaius process run under a dedicated operating-system
account. That account owns its checkout and index but has no access to other
users' home directories. Its SSH credential is restricted to Git transport for
the canonical memory repository.

Deployment-specific account names, hostnames, tunnel identifiers, keys, and
service definitions do not belong in this repository.

## MCP Profiles

`gaius-mcp` accepts `--profile local|remote`. `local` is the default and
preserves the existing stdio MCP behavior.

The remote profile advertises exactly seven tools:

| Tool | Access | Behavior |
|---|---|---|
| `search_memory` | read | Pull, then search store-internal content |
| `get_project_state` | read | Pull, then read existing state without creating files |
| `list_projects` | read | Pull, then list projects |
| `read_doc` | read | Pull, then read one store-confined text document |
| `add_memory` | write | Pull, write, commit, and push |
| `handoff` | write | Pull, write, commit, and push |
| `log_decision` | write | Pull, write, commit, and push |

The remote profile does not advertise `sync`, `run_task`, or `task_status`.
Synchronization is a server guarantee rather than an agent instruction.

## Synchronization

Remote operations are serialized with one process-wide lock and one advisory
lock file in the checkout. This prevents concurrent MCP calls or accidental
duplicate server processes from racing Git or the derived index.

The sync implementation is separated into pull and publish phases:

1. Require a clean checkout or a verified unpublished commit from a previous
   failed push.
2. Retry publication of a verified unpublished commit.
3. Pull from the configured upstream. Abort on any error or conflict.
4. Run the requested read or write.
5. For a write, stage all checkout changes, commit them, and push.
6. Return success only after the push succeeds.

A read never creates memory files. A write whose file mutation succeeds but
whose commit or push fails returns a structured failure:

```json
{
  "ok": false,
  "saved_local": true,
  "published": false,
  "error": "..."
}
```

The local file or commit is retained for recovery. Before committing a remote
write, the server records its request identifier in
`.gaius/remote-pending.json`. After commit, it adds the resulting commit SHA.
The marker is removed only after a successful push. On the next call, the
server retries publication only when the marker's SHA equals `HEAD` and the
checkout is clean. Any mismatch, uncommitted state after a failed commit, or
otherwise unexplained dirty checkout fails closed and requires manual repair;
the server never guesses which local changes are safe to publish.

Git conflicts and missing remotes are remote-profile errors. They remain
non-fatal in the ordinary local product where offline and remote-less operation
must continue to degrade gracefully.

## Data Boundary

Remote profile arguments are treated as untrusted:

- A project or topic is one path segment. Empty values, `.`, `..`, NUL bytes,
  `/`, and `\` are rejected.
- Resolved store paths must remain beneath the configured memory root.
- `read_doc` accepts a document ID or store-relative path only. Absolute paths
  and external-root paths are rejected.
- Document IDs and paths reject glob metacharacters. Every relative path
  component is validated before resolution.
- Remote `read_doc` serves text memory documents only and enforces an output
  size limit.
- Remote search filters out any result whose resolved path is outside the
  memory store, even if an old or contaminated index contains it.
- The remote profile ignores configured external roots.
- The remote profile never registers task tools, even if `task_command` exists
  in the selected configuration.

Initial limits are conservative and configurable only in code:

- query: 4 KiB;
- memory or handoff text: 64 KiB;
- decision text: 16 KiB;
- project/topic/tag segment: 128 characters;
- tags: 32; and
- search results: 1 to 50.

Limit errors are explicit and do not truncate writes.

## Tool Metadata

Read tools are annotated as read-only, non-destructive, idempotent, and
closed-world. Write tools are annotated as mutating, non-destructive,
non-idempotent, and closed-world.

The server descriptions explain that write success means the Git push
completed. Tool output does not claim cross-host durability otherwise.

## Logging

Remote operational logs may contain:

- timestamp;
- tool name;
- duration;
- success or failure category; and
- a generated request identifier.

They must not contain tool arguments, memory text, search snippets, document
contents, access tokens, tunnel credentials, or complete tool results.

## Installation And Operation

Remote MCP is an optional, manual deployment:

1. Create a dedicated OS account and memory checkout.
2. Give it git-only access to the canonical repository.
3. Install Gaius in its own virtual environment.
4. Configure the store and a local embedder, with no external roots or task
   command.
5. Verify the remote MCP profile locally with MCP Inspector.
6. Configure the chosen authenticated tunnel.
7. Run the tunnel client and MCP server as a supervised service.
8. Associate the tunnel only with the intended account or workspace.
9. Exercise read and write tests from the hosted surface.

The tunnel runtime credential has use-only permissions where the provider
supports them. It is stored outside Git with owner-only permissions. Stopping
the supervised service and revoking the runtime credential is the kill switch.

The README documents that remote MCP is explicit opt-in and that memory returned
to a hosted agent necessarily leaves the user's machines and enters that
provider's data boundary.

## Compatibility

The released dependency declaration `mcp>=1.0` currently resolves to MCP 2.0,
which removed the import path used by Gaius. Until Gaius deliberately migrates
to the new SDK API, the package must declare the compatible range
`mcp>=1.0,<2`.

This compatibility pin is required for both existing local MCP and the new
remote profile. Migrating to MCP 2.x is separate work.

## Testing

Implementation follows test-driven development and covers:

- the exact local and remote tool lists;
- MCP annotations for every remote tool;
- project, topic, tag, and document traversal attempts;
- absolute and external-root `read_doc` rejection;
- contaminated external search-result filtering;
- read operations pulling before access;
- write operations pulling before mutation and pushing before success;
- pull, commit, and push failure responses;
- recovery of a committed but unpublished write;
- refusal of unexplained dirty checkouts;
- serialization of concurrent calls;
- input and output limits;
- preservation of existing local MCP behavior;
- fresh-install compatibility with the supported MCP dependency; and
- the full test suite on Linux and macOS.

Deployment verification adds:

- an MCP initialization handshake under the dedicated account;
- confirmation that the account cannot read other users' homes;
- tunnel health and restart checks;
- a hosted-agent search and full-document read; and
- a hosted-agent write that becomes visible to another Gaius host after sync.

## Future Vendor-Neutral Gateway

A later phase can put the same remote profile behind stable HTTPS and OAuth 2.1
for hosted surfaces that cannot use an outbound private tunnel. That gateway
must add protected-resource metadata, authorization-code flow with PKCE,
audience and scope validation, token revocation, rate limiting, and
provider-specific ingress controls.

Read, write, and task execution must be separate scopes. Task execution remains
out of scope until it has its own approval and operating-system isolation
design.
