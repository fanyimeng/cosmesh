# CosMesh 0.6

Continue the same work across devices and agents. Send selected material, view correspondence by person, and file incoming messages on your own terms.

```sh
cosmesh thread find Agenda
cosmesh thread continue THREAD_ID --ready
cosmesh collect add THREAD_ID PROJECT_DIR --personal
cosmesh collect once
cosmesh thread fetch THREAD_ID --agent AGENT --workdir DIRECTORY --files
cosmesh thread send THREAD_ID --to ACCOUNT --body TEXT --attach FILE --dry-run
cosmesh thread inbox
cosmesh thread activity THREAD_ID
```

[Agent guide](cosmesh/skills/cosmesh/SKILL.md) · [Components](components/README.md) · [Upgrade notes](cosmesh/manual/upgrade.md)
