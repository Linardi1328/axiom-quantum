# Axiom Quantum — QQQ/IWM Historical Context Source Policy

Scope: RIC-154 / PR #98 targeted security remediation. This is an explicit,
quarantine-only historical capture capability, not authorization to retrieve data,
verify historical eligibility, fit models or trade. This remediation uses fake
providers and offline fixtures exclusively. Fresh independent review remains a
merge gate; the owner permits independent GPT review for this cycle if CodeRabbit
is rate limited. Passing tests or review does not itself authorize merge/download.

## Separate contracts and frozen boundaries

SPY Phase 1 `AcquisitionRequest`, canonical/manifest schemas, source checksums,
dataset IDs, provider parameters and shared `DatasetStore` behavior remain frozen.
The context request uses the read-only stock-bars adapter with a context-only
transport branch. Physical storage and publication use a separate `ContextStore`:
the inherited SPY implementation cannot provide the required context guarantees
without changing frozen behavior. No new third-party dependency is introduced.

The research gates are distinct:

| Path | Required inputs / authority |
| --- | --- |
| Original MI-2A/MI-2B and `assess_binary_context_preflight` | Original QQQ/IWM/VIX/US10Y evidence; unchanged four-source gate |
| Phase 3C-A | `aq-p3ca-qqq-iwm-synthetic-eligibility-v1`; two-source **synthetic-only** fixtures, never real-data certification |
| Phase 3C-B | `aq-p3cb-synthetic-comparison-v1`; zero-argument internally generated synthetic experiment, no acquired-data input |
| Real-data two-source eligibility | **Unimplemented and unauthorized**; requires separately reviewed rights, PIT/as-of adjustment and provenance evidence |

No VIX/US10Y proof is fabricated. Acquired artifacts cannot be converted into
Phase 3C-A synthetic proofs or passed into the Phase 3C-B runner. Phase 3B evidence
and Phase 2 protected boundaries are unchanged. Any eventual separately authorized
real runner must verify the frozen SPY parent and mechanically apply the existing
Phase 2 exclusion contract **before constructing real labels**. No such runner,
CLI, scheduler, production model registration or execution bridge is added here.

## Incompatible v2 quarantine schema

- Manifest schema: `aq-qqq-iwm-context-acquisition-v2`.
- Raw envelope schema: `aq-qqq-iwm-context-raw-v2`.
- Dataset ID: `aq-ctx-v2-` plus 24 lowercase hex characters.
- Fixed policy: one QQQ or IWM symbol, `alpaca`, SIP, `1Day`, `all`, `asof=-`.
- Request acknowledgment must be the actual boolean `True`; unknown fields,
  noncanonical policy values and forged/unvalidated model updates are rejected.
- Daily bars identify New York midnight and cover the exact contiguous XNYS
  sessions. Unsupported/empty/future/incomplete ranges fail before client access,
  using the injected clock rather than model-construction wall-clock time.

Version 1 manifests and IDs are explicitly rejected by this verifier. There is no
migration or compatibility reinterpretation. Existing v1 artifacts or any prior
five-session pilot remain unverified and are not inspected, upgraded or certified
by this remediation. Do not relabel earlier evidence with v2 metadata.

Dataset identity binds canonical content, request policy, schema and code/runtime
lineage. Different retrieval timestamps/raw evidence under the same canonical
identity conflict rather than overwrite. Manifests record Git SHA, exact relevant
source checksums, Python/package and dependency versions. Self-checksums cover
these fields. This verifier conservatively requires the **same recorded code and
runtime**, including Git SHA/source hashes, as the supported current implementation.
To inspect v2 artifacts later, use the matching checkout/environment or a separately
reviewed verifier revision; mismatching runtime evidence fails closed.

## Physical storage, concurrency and recovery

Only ignored, untracked destinations under
`data/{raw,canonical,manifests}/alpaca/{QQQ,IWM}/1Day/sip/all/` are allowed. Actual
Git ignore status is checked for the destination directory before provider access
and again at publication. A filename-only ignore exception is insufficient.
Data-root, ancestor and final-path symlinks (including dangling links), hardlinks,
nonregular files, unowned and group/world-writable storage directories are refused.
POSIX descriptor-relative no-follow opens pin each directory; ancestry is checked
again before staging/publication and reads. Payload publication uses atomic
no-clobber hard links, not `replace()` or an early `resolve()` decision alone.

A persistent ignored `.context-acquisition-v2.lock` under `data/manifests/` provides
interprocess coordination. Lock creation is exclusive, followed by a no-create
existing open, avoiding a demonstrated concurrent O_CREAT race on macOS. A global
context lock intentionally serializes all context datasets; a simultaneous attempt
fails closed and must be retried manually. Verification obtains the existing lock
without creating it and performs no artifact writes. Lock files are never removed
automatically. The manifest is published **last**, as the completion marker.

Rollback removes only inode/device identities published by the failing transaction,
using its pinned descriptors. It never infers ownership from initial absence and
never deletes unrelated/replaced files. Missing subsets, orphan staging files or
conflicting bytes are `recovery_required`. Interrupted writes do not trigger
automatic resubmission, deletion or artifact adoption. An operator must stop other
writers, preserve the entire partial set, inspect ownership and checksums offline,
and obtain explicit maintenance approval before manual quarantine/removal or retry.
A missing lock likewise requires inspection, not automatic read-path repair.

Threat boundary: this is an owner-controlled local POSIX filesystem (macOS/Linux),
not a sandbox against a hostile process with the same account/root permissions.
Such a process can read data, rewrite code or relocate directories between any
syscalls. Descriptor pinning prevents symlink redirection, repeated checks detect
ancestry changes, and directory ownership/mode checks exclude other-user write
access; this does not claim host-compromise resistance or arbitrary same-UID
relocation immunity. Windows and untrusted/network filesystems are not certified.

## Strict wire data, numbers and error boundary

Raw responses allow only `pages`; pages allow only `bars` and an explicit
`next_page_token`; bars contain only the requested symbol. Records require
`t,o,h,l,c,v`, with optional numeric `n,vw`. Arbitrary fields, nested headers,
auth/account metadata and corporate-action payloads are rejected, not silently
stored. Supplied credential values are checked against accepted scalar data and
serialized artifacts. Credentials must be supplied explicitly from a separately
verified market-data-only source; separate environment names do not establish
account-level permission isolation. No credential lookup occurs automatically.

Provider/API/SDK/access method/source timezone and sanitized request fields must
match the supported policy exactly. API version is `v2`, SDK is the installed
`alpaca-py` version, source timezone is UTC and the documented low-level stock-bars
access method is fixed. Unexpected SDK/transport errors expose fixed classified
public exceptions, with no raw cause/context or sensitive traceback text. The
original SPY exception behavior remains unchanged.

Budgets: at most 64 pages, 20,000 rows and 32 MiB per serialized artifact; 120 seconds
elapsed per acquisition, at most three retries and a 30-second request timeout.
The context SDK client's internal retries/delays are disabled; only the outer
context retry loop owns the attempt budget. Other SDK clients are unchanged.
The real context SDK session caps each request/retry timeout to remaining budget;
monotonic checks run before/after requests and retry delays. Arbitrary injected
provider Python code must cooperate with timeouts; this is not a process-killing
watchdog against a noncooperative fake/client or a hostile server trickling bytes
within a socket read timeout. Over-budget results are rejected and never persisted.
Numbering, prior/next token continuity, uniqueness, explicit terminal null and
per-page/raw row counts are checked during acquisition **and** artifact verification.

Accepted prices are JSON numbers or decimal text of at most 128 characters,
38 coefficient digits, exponent -18..18 and adjusted exponent -12..12, strictly
positive and finite. Oversized integer objects are rejected before string conversion.
Values are parsed exactly, OHLC relations checked before serialization, and formatted
without ambient decimal-context normalization/rounding. Volume/trade count are
integers or digit text in 0..2^63-1; booleans and fractional values are rejected.
Neither excessive exponents nor coefficient strings are expanded into unbounded
fixed-point allocations. Raw/canonical reconstruction and exact serialization are
verified, including malformed/recomputed manifests, metadata and swapped paths.

## Retrieval provenance and remaining authorization

`request_started_at` is captured before provider access. `retrieval_completed_at`
is captured after it returns, separately from the raw provider's completion stamp.
All are aware UTC instants ordered start <= provider completion <= outer completion.
Neither timestamp establishes when a value was historically knowable.

Alpaca SIP entitlement, permitted storage/retention/derived use, historical ticker
continuity and as-of corporate-action/revision history must be independently
established. `adjustment=all` downloaded now may encode later actions. A checksum,
current download, caller boolean or internal verifier proves neither licensing,
historical availability nor independent provider authenticity. Every artifact keeps:

```text
point_in_time_status = UNVERIFIED
usage_rights_status = UNVERIFIED
execution_authority = none
disposition = NO CANDIDATE PROMOTION
```

No actual provider request, credentials, historical download, protected outcomes,
real-data fitting, model promotion, shadow inference, paper orders or live trading
are authorized in this cycle. Separate explicit owner authorization is required
for any subsequent download, real-data eligibility work or historical experiment.
