# CosMesh 0.6

工作以 Thread 为单位跨设备接续。按内容投递、按人查看往来，来信可推荐关联或暂不归档。

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
