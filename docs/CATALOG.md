# Catalog architecture

UtterMux separates stable model metadata from live provider discovery.

## Generated local-model catalog

The reviewed sources are:

- `catalog/catalog.toml` for curated model artifacts;
- `catalog/platform-variants.toml` for platform-specific runtime variants;
- `catalog/sources/piper.json` for the pinned Piper discovery snapshot.

`scripts/catalog/build_catalog.py` deterministically produces
`catalog/v2/catalog.json` and `docs/MODELS.generated.md`. The output separates
model families, runtime variants, voices, artifacts, and platform eligibility.
URLs, SHA-256 hashes, sizes, licenses, and provenance are release data. CI fails
when either generated file is stale.

Linux currently reads the reviewed TOML catalog at runtime and installs the
schema-2 JSON as the cross-platform contract. Android embeds a copy of the
schema-2 JSON and projects only variants marked for Android. The two projects
therefore share identity and artifact metadata while retaining different
runtimes and acceptance decisions where required.

Updating a local model follows this sequence:

1. Review or refresh a pinned source snapshot outside the generator.
2. Add the artifact and platform policy to a reviewed TOML source.
3. Regenerate JSON and Markdown without network access.
4. Run schema, checksum, synthesis, cancellation, and reader tests.
5. Copy the accepted generated JSON into the Android repository when the
   change affects Android.

## Online-provider catalogs

Online voice catalogs are not committed as model variants. They can depend on
an account, region, subscription, and the provider's current API. Each app has
a reviewed provider adapter and discovers available voices at runtime. The UI
then overlays credentials, readiness, cost, and configured routes on those
records.

This keeps releases reproducible without presenting a stale cloud voice list.
Provider support itself remains explicit in the READMEs and code; successful
discovery never enables a paid provider as an automatic fallback.

## What is shared and what is platform-specific

| Data or behavior | Shared | Platform-specific |
| --- | --- | --- |
| Model family and stable variant IDs | Yes | — |
| Artifact URL, hash, size, and license | Yes | — |
| Voice and BCP-47 language metadata | Yes | A platform may expose a subset |
| Runtime implementation | Contract name | JNI/Android service or Linux broker/module |
| Variant acceptance | Test criteria | Hardware and client results |
| Cloud voice list | Provider contract | Discovered live by each app |
| Installed, configured, and active state | State schema | Stored locally on each device |

The Android repository is
[`anaxonda/uttermux-android`](https://github.com/anaxonda/uttermux-android).
