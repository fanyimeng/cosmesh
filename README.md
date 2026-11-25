# CosMesh 0.7.0

Keep records and registered files together in a Thread. When sending a message you can give named recipients ongoing read access to that work. Recipients can browse the latest progress or open the version you sent.

## Install and update

On macOS or Linux, use the installation command supplied by your Hub maintainer. Existing stable-channel installations use `cosmesh client update`. For an offline package, unpack it and run `python3 install.py install --skills cosmesh`. To upgrade an existing installation run `python3 install.py upgrade` in the new package directory, keeping any custom `--prefix`.

The Windows client targets Windows 11 x64 with 64-bit CPython 3.12 and Git for Windows. Hub connections use OpenSSH. To get the offline package, replace HUB with your SSH alias and /path/to/releases with the release directory supplied by your maintainer. Run these commands in PowerShell.

```powershell
scp HUB:/path/to/releases/cosmesh-0.7.0-win32-AMD64-py312.zip .
Expand-Archive .\cosmesh-0.7.0-win32-AMD64-py312.zip -DestinationPath .
Set-Location .\cosmesh-0.7.0-win32-AMD64-py312
$prefix = Join-Path $env:LOCALAPPDATA 'CosMesh'
py -3.12 -I -X utf8 .\install.py install --prefix "$prefix" --skills cosmesh
& "$prefix\bin\cosmesh.cmd" guide show upgrade
```

For subsequent Windows updates, unpack the new package and run the installed `cosmesh.cmd client update --release DIR`. If you installed the earlier 0.7.0 preview, use a separate program prefix for the release and keep the data directory or `--root` for the work you want to continue. Native Windows validation of NTFS permissions and two-account OpenSSH operation remains pending.

First installation and version upgrades display the short usage guide. Reopen it with `cosmesh guide show upgrade`. Use the Windows launcher above wherever an example uses cosmesh.

```sh
cosmesh thread find Agenda
cosmesh thread continue THREAD_ID --ready
cosmesh thread note THREAD_ID --body "First check complete"
cosmesh thread send THREAD_ID --to ACCOUNT --body "Please review the latest progress"
cosmesh thread inbox
cosmesh thread mail open MESSAGE_ID --records
cosmesh thread mail open MESSAGE_ID --at-send --files
cosmesh collect status
```

[中文说明](README.zh-CN.md) · [Quick start](cosmesh/manual/upgrade.md) · [Sharing](cosmesh/manual/sharing.md) · [Agent guide](cosmesh/skills/cosmesh/SKILL.md) · [Components](components/README.md)
