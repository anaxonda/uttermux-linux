# Security policy

Please report credential exposure, unsafe model extraction, unauthenticated
network listeners, or other security problems privately to the maintainers
before opening a public issue. A dedicated security contact will be added when
the public repository is created.

UtterMux treats online-provider credentials, cloned-voice recordings, and
spoken text as sensitive. Diagnostics must not contain document text or secret
values. Credentials are stored outside the main configuration with user-only
permissions. The KOReader compatibility listener binds only to loopback.

Model downloads must use HTTPS and an immutable SHA-256 digest. Custom model
manifests describe data files only; they cannot run installation commands.

The project is in active development and does not yet promise a fixed security
support window. Release notes will identify supported versions once public
releases begin.

