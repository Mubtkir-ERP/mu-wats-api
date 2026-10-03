# frappe_whatsapp 1.1.1

## WhatsApp Message default channel hotfix

- Fixed new `WhatsApp Message` documents showing `Meta` even when `WhatsApp Settings > Default Channel` is `Evolution`.
- Root cause: the Channel Select options started with `Meta`, so Frappe selected the first option automatically even though the field default had been cleared.
- Channel options now include an explicit blank first option.
- The existing form logic can therefore load and display the configured global Default Channel.
- Added a migration patch for sites that already installed 1.1.0. It clears the old Custom Field default, updates its options, and clears DocType caches.
- Manual per-message override remains supported after the form loads.
