# Noofy Verified Publishing Process

Status: Phase 6 definition for marketplace readiness.

This document defines what "Noofy Verified" means operationally. It does not implement the full in-app marketplace.

## Goals

- Make Noofy Verified packages repeatable, reviewable, and auditable.
- Ensure a package cannot become Noofy Verified only by declaring `trust_level: "noofy_verified"`.
- Keep the trusted backend free of community custom-node imports during review, import, and runtime preparation.
- Preserve beginner-friendly UI language while keeping technical trust diagnostics available behind developer details.

## Required Package Evidence

A Noofy Verified package must include or reference:

- package identity:
  - `publisher_id`
  - `package_id`
  - `version`
  - `trust_level: noofy_verified`
  - package source
- canonical package payload hash
- graph hash
- dashboard schema hash
- capsule lock hash
- required model hashes and source URLs
- custom-node source refs and source content hashes when custom nodes are used
- dependency lock hashes with wheel hashes
- runtime profile compatibility metadata
- successful smoke-test evidence for supported runtime profiles
- signature or signed registry metadata produced by Noofy-controlled publishing keys

The package signature payload excludes signature evidence fields and includes hashes for package metadata, graph, dashboard, capsule lock, and export report. This keeps signatures stable while preventing evidence fields from signing themselves.

## Publishing Gates

Before a package can be published as Noofy Verified:

1. Validate package schema and dashboard schema.
2. Validate package identity and namespace ownership.
3. Verify every model source has expected hash and size metadata.
4. Verify every non-core node is pinned to an approved source ref and source content hash.
5. Generate or validate dependency locks with hashes.
6. Reject arbitrary setup scripts, unpinned repositories, sdists/native builds where policy disallows them, and unsupported launch options.
7. Prepare the workflow in an isolated staged runtime.
8. Run dependency import smoke, custom-node registration smoke, runner health smoke, and workflow execution smoke.
9. Run supported runtime-profile compatibility checks.
10. Record the publishing report and signing payload hash.
11. Sign the package or registry metadata with a Noofy-controlled signing key.
12. Publish package metadata, trust evidence, and registry snapshot metadata atomically.

## Signing Requirements

The current local implementation supports `hmac-sha256` trust roots for policy tests and local development. Public marketplace publishing must use asymmetric signatures so product builds only need public verification keys.

Production signing must support:

- stable key IDs
- public-key trust-root distribution
- key rotation
- key expiry
- revocation metadata
- registry snapshot identity
- policy-version compatibility
- tamper-resistant canonical payloads

No production trust keyring may contain private signing keys or HMAC secrets.

## Registry Locked Publishing

Registry Locked packages are not Noofy Verified, but they must have registry evidence that locks source metadata:

- registry ID
- registry snapshot hash
- package payload hash
- resolved custom-node package IDs
- pinned source refs
- source content hashes
- dependency policy version
- signed registry metadata

Registry Locked workflows may be prepared automatically only when current product policy permits it for the user's device/backend and the signed registry metadata verifies.

## Release Roles

At minimum, publishing should separate these responsibilities:

- package author/exporter: produces the workflow package and dashboard
- verifier: runs automated checks and smoke tests
- reviewer: approves Noofy Verified status and release notes
- signer: signs package or registry metadata with controlled keys
- publisher: publishes package and registry metadata

One person may hold multiple roles during development, but the artifacts and logs should preserve which role performed which action.

## Revocation And Replacement

Noofy must be able to mark a published package or key as revoked when:

- a signing key is compromised
- package metadata is discovered to be incorrect
- a model source is removed or replaced unexpectedly
- a custom-node source is compromised
- a dependency lock includes a vulnerable or policy-blocked artifact

Revocation should not silently delete local user data. It should prevent new preparation, show a beginner-friendly blocked state, and keep developer details for the reason.

## Completion Criteria

The publishing process is complete for Phase 6 when:

- production asymmetric verification exists in the backend
- public trust roots can be loaded without private key material
- signed Noofy Verified and Registry Locked fixtures pass import and preparation gates
- tampered or revoked fixtures fail closed before source resolution or runtime preparation
- documentation and UI avoid claiming that unverified community code is safe
