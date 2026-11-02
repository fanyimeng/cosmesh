# TUI demo / 终端界面演示

An optional, standalone interface prototype using Python's standard-library `curses` module. All data is synthetic and held in memory; exiting resets it. The normal product interface is your own AI agent and the CLI.

本演示用于浏览界面和讨论操作方式，采用合成数据，退出后重置。日常使用以个人 Agent + Skill + CLI 为主。

```sh
python3 demos/tui_demo.py
# Non-interactive preview / 非交互预览：
python3 demos/tui_demo.py --snapshot
python3 demos/tui_demo.py --snapshot --product cosmesh
```

Use a UTF-8 terminal with at least 80 columns × 24 rows. `1`/`2` switch products; arrows or `j`/`k` navigate; `Enter` opens; `n` creates; `t` converts; `a` archives; `d` recycles; `r` restores; `?` shows help; `q` exits. CosMesh mode is read-only in this prototype.

This is an earlier Project/Task/Update UI sketch, separate from the persistent Thread CLI. See the [quick start](../docs/quickstart.md) for the current workflow.
