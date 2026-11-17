# Lid Awake

Keep a MacBook awake with the lid closed, for a time you choose. When the timer ends, normal sleep comes back.

The screen is allowed to turn off. Downloads, SSH sessions, and local agents keep running while the display sleeps or the lid is shut.

## How it differs

A MacBook sleeps when the lid closes, unless it is plugged into power with an external display and a keyboard or mouse. `caffeinate` does not change that.

`pmset -a disablesleep 1` does keep the Mac awake with the lid closed and nothing else attached. That switch stays on until someone turns it off, including after a restart. Lid Awake turns it on for a time you choose, from 1 minute to 24 hours, then turns sleep back on by itself.

The display keeps its own timer, so the screen can go dark while the computer stays awake. Closing the window leaves the timer running.

## What it does

- Toggle between normal lid sleep and staying awake.
- Timer from 1 minute to 24 hours. Presets are 15 minutes, 30 minutes, 1 hour, 2 hours, and 4 hours.
- The built-in display still follows the Mac display-sleep setting. Closing the lid turns that screen off immediately. The computer stays awake until the timer ends.
- The window is only the control. Closing it does not turn sleep back on. A per-user login agent keeps the timer and restores sleep even after a restart, once you log in.
- The first switch asks for an administrator password once. The sudo rule allows only two commands: `pmset -a disablesleep 1` and `pmset -a disablesleep 0`.

macOS has no setting that disables lid sleep alone. `disablesleep` is the switch that covers a closed lid with no external display. Idle system sleep is off for the same stretch. Display sleep is a separate timer and is left alone.

## Build

Requires macOS 13 or later, Xcode command line tools, and Python 3 (`/usr/local/bin/python3`, Homebrew, or `/usr/bin/python3`).

```bash
./build-app.sh
```

This writes `dist/Lid Awake.app`. Move that app to `/Applications` and open it. The first launch of an app you built or downloaded yourself is Right Click, then Open.

The timer does not run on the login screen. After you log in, if the deadline has already passed, sleep is turned back on.

## Remove

Turn sleep back on from the window first, then:

```bash
launchctl bootout gui/$(id -u)/com.gidon.lid-awake
rm -f ~/Library/LaunchAgents/com.gidon.lid-awake.plist
rm -rf ~/Library/Application\ Support/LidAwake
osascript -e 'do shell script "rm -f /etc/sudoers.d/lid-awake" with administrator privileges'
```

Log: `~/Library/Logs/lid-awake.log`

## Why this is not a Docker image

Docker Desktop on a Mac runs containers inside a Linux virtual machine. That machine cannot call `pmset` or change the Mac power settings. The switch has to run on macOS itself.
