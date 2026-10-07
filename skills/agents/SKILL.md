---
name: agents
description: Create a specialized agent (a Hermes profile) or a topic chat for the Sheldon app, and choose between them. Load before running hermes profile create or calling the sheldon_chats tool.
version: "1"
metadata:
  hermes:
    tags: [sheldon, agents, profiles, chats]
---

# Specialized agents and topic chats in Sheldon

The user reads you in Sheldon, their iPhone and Mac app. Its chat list has the main chat (you), one chat
per specialized agent, and topic chats. When the user asks for a new agent, a new "bot" or a separate
chat, pick the right one, create it, and tell them in one sentence where it is.

## Which one

- **Topic chat**: a separate thread with the same agent: same personality, memory, skills, tools and
  model. For a subject the user wants to keep apart ("Podcast", "Travaux", "Voyage à Lisbonne"). Instant,
  cheap, removable, and its history stays in Hermes.
- **Specialized agent**: a Hermes profile with its own personality (`SOUL.md`), memory, skills, tools
  and model. For a role that must behave differently over time (a coding agent, a writer with its own
  voice, an agent that watches a field every morning).
- Create an agent only when the user asks for an agent or a bot, or when the role needs its own
  instructions or model. Otherwise, a topic chat. If you are not sure, ask with clarify:
  "Un chat de sujet ou un agent à part ?".

## Create a topic chat

Call the `sheldon_chats` tool (the `sheldon` toolset):

- `{"action": "add", "title": "Podcast"}`: a topic chat with the main agent.
- `{"action": "add", "title": "Podcast", "agent": "<profile>"}`: a topic chat with a specialized agent.

The title is one line of 60 characters at most, and not the title of an existing chat. The chat
appears in Sheldon at once. `{"action": "list"}` lists the chats and their ids;
`{"action": "remove", "conversation": "<id>"}` removes a topic chat (its history stays in Hermes).
If the tool refuses, tell the user why in one sentence.

## Create a specialized agent

1. Create the profile, cloned from yours so it can answer right away:

   ```bash
   hermes profile create <name> --clone --description "<one or two sentences: what it is good at>"
   ```

   - `<name>`: lowercase letters, digits, `-` or `_`, starting with a letter or a digit, 64
     characters at most (`podcast`, `veille-design`). It becomes the agent's id; its chat in
     Sheldon is `agent-<name>`.
   - `--clone` copies your `config.yaml`, `.env`, `SOUL.md` and skills: same model, same keys.
     `--clone` also copies your memory (`memories/MEMORY.md` and `memories/USER.md`): the new agent
     already knows the user's first name and language, so do not ask the user's first name again.
     Without it, the profile starts empty and cannot answer until it is configured.
   - Sheldon shows the description under the agent's name: write it for the user, in the user's language.
2. Its name in Sheldon: the profile id, unless its `profile.yaml`
   (`~/.hermes/profiles/<name>/profile.yaml`) has a `display_name`. To show "Podcast" rather than
   "podcast", add the line `display_name: Podcast` to that file.
3. Its model: the one copied by `--clone`. Change it only if the user asks:
   `hermes -p <name> config set model.default <model>`.
4. Its personality: edit `~/.hermes/profiles/<name>/SOUL.md` (who it is, what it does, how it
   answers), if the user described the role.

The new agent appears in Sheldon's list by itself, in less than a minute, on the iPhone and on the
Mac. Tell the user so. Several agents show in Sheldon only when the default profile's gateway serves
them all, with the Sheldon plugin enabled on the default profile only. Secondary agents need Hermes 0.21.1 or newer,
where the default profile's gateway serves every profile; on 0.20.4 Sheldon only sees the main agent, even with `gateway.multiplex_profiles: true`.
If the new agent does not appear within a minute, the user restarts the gateway.

## Limits

- If the user says the new agent's chat does not answer, they restart the gateway with
  `hermes gateway restart` on Hermes's computer. Never run it yourself: it would stop the gateway that
  runs this conversation.
- Removing an agent (`hermes profile delete <name>`) closes its chat in Sheldon. Do it only when
  the user asks, and say that its memory and history go with it.
