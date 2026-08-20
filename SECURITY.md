# Security policy

Please report credential exposure, unsafe model extraction, unauthenticated
network listeners, or other security problems through the repository's
[private security advisory form](https://github.com/anaxonda/uttermux-linux/security/advisories/new)
instead of opening a public issue.

UtterMux treats online-provider credentials, cloned-voice recordings, and
spoken text as sensitive. Diagnostics must not contain document text or secret
values. Credentials are stored outside the main configuration with user-only
permissions. The KOReader compatibility listener binds only to loopback.

Model downloads must use HTTPS and an immutable SHA-256 digest. Custom model
manifests describe data files only; they cannot run installation commands.

The project is in active development and does not yet promise a fixed security
support window. Release notes will identify supported versions once public
releases begin.
