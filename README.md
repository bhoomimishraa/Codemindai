# CodeMind AI: Intelligent Engineering Workspace

Open `index.html` in any modern browser. No build step or server needed.

- Register with an email and a password (6+ characters). Accounts and projects are stored in your browser's localStorage.
- AI features (generate, debug, explain, tests, docs, refactor) use Claude when the page runs inside claude.ai. Opened as a plain local file, they fall back to offline heuristics.
- To connect your own model, edit the `ai()` function in `index.html` and call your API there.
- "Download" buttons fall back to copying to the clipboard when the claude.ai download capability is unavailable.
