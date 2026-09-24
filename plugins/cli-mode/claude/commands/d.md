---
description: Send this prompt to the active CLI-MODE agent
argument-hint: "<prompt for the active agent>"
disable-model-invocation: true
---
This message is for the active CLI-MODE agent, and CLI-MODE's hook has already forwarded it unchanged. The
agent's reply reaches the user only through the relay described in CLI-MODE's context for this turn: follow it,
posting what the relay returns exactly as returned. The task itself belongs to the agent, so it is not done,
planned or split here. If there is no CLI-MODE context for this turn, the hook did not run: tell the user to make
sure the cli-mode plugin is enabled (/plugin), check /hooks, then run /reload-plugins or start a new session.
