---
description: Open CLI-MODE or run a CLI-MODE control (bind, stop, menu, model, effort, access, mode, queue, cancel, resume, reset, help)
argument-hint: "[agent | bind <agent> | stop | menu | model | effort | access | mode | queue | cancel | resume | reset | help]"
disable-model-invocation: true
---
CLI-MODE's hook handles /cli. Most controls it answers itself; for the rest, CLI-MODE's context for this turn
gives the exact command and how its result is shown, and that context is complete: follow it as given, with no
other tools or skills. If there is no CLI-MODE context for this turn, the hook did not run: tell the user in one
short paragraph to make sure the cli-mode plugin is enabled (/plugin), accept this folder's workspace trust
prompt, check /hooks (hooks do not run with disableAllHooks or --bare), then run /reload-plugins or start a new
session and type /cli again, and do not act on the arguments.
