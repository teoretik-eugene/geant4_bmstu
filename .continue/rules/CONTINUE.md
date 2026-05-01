# Continue — Project Guide

> **Auto-loaded by Continue:** This file is automatically included in the AI assistant's context when working in this repository. Keep it accurate and up to date.

---

## 1. Project Overview

**Continue** is an open-source AI code assistant that integrates directly into developer IDEs. It provides chat, inline code editing, autocomplete, and context-aware assistance powered by any LLM (local or cloud-based).

### Key Technologies

| Layer | Technology |
|---|---|
| Core engine | TypeScript (Node.js) |
| VS Code extension | TypeScript, VS Code Extension API |
| JetBrains plugin | Kotlin, IntelliJ Platform SDK |
| GUI (webview) | React, TypeScript, Vite, Tailwind CSS |
| Binary server | Node.js (pkg-bundled) |
| Docs | Docusaurus |
| Testing | Jest (core), Vitest (gui) |

### High-Level Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                         IDE Extension                            │
│   ┌──────────────────┐         ┌──────────────────────────────┐  │
│   │  VS Code / IDEA  │◄───────►│    Webview GUI  (React)      │  │
│   │  Extension Host  │         │  gui/src/                    │  │
│   └────────┬─────────┘         └──────────────────────────────┘  │
│            │ IPC / WebSocket                                      │
└────────────┼─────────────────────────────────────────────────────┘
             │
┌────────────▼─────────────────────────────────────────────────────┐
│                        Core (core/src/)                          │
│  ┌─────────────┐  ┌──────────────┐  ┌──────────────────────────┐ │
│  │  LLM Layer  │  │  Autocomplete│  │   Context Providers      │ │
│  │ (llm/llms/) │  │  Engine      │  │ (context/providers/)     │ │
│  └─────────────┘  └──────────────┘  └──────────────────────────┘ │
│  ┌─────────────┐  ┌──────────────┐  ┌──────────────────────────┐ │
│  │  Indexing   │  │  Config      │  │   Slash Commands         │ │
│  │  Engine     │  │  Handler     │  │   (slash/)               │ │
│  └─────────────┘  └──────────────┘  └──────────────────────────┘ │
└──────────────────────────────────────────────────────────────────┘
             │
┌────────────▼─────────────────────────────────────────────────────┐
│              LLM Providers (OpenAI, Ollama, Anthropic, …)        │
└──────────────────────────────────────────────────────────────────┘
```

The **core** package is IDE-agnostic. IDE extensions communicate with it through a well-defined **protocol** (see `core/src/protocol/`). The **GUI** runs inside a webview and communicates with the extension host through the same protocol.

---

## 2. Getting Started

### Prerequisites

- **Node.js** ≥ 20.x
- **npm** ≥ 10.x  
- **VS Code** (for extension development)
- **Java 17+** & **Gradle** (only for JetBrains plugin)
- **Git**

### Installation

```bash
# 1. Clone the repo
git clone https://github.com/continuedev/continue.git
cd continue

# 2. Install all workspace dependencies (runs scripts in core, gui, extensions)
cd core && npm install
cd ../gui && npm install
cd ../extensions/vscode && npm install
```

> **[VERIFY]** A root-level `npm install` or workspace bootstrap script may exist — check if a top-level `package.json` scripts section orchestrates this.

### Running the VS Code Extension in Development

```bash
# 1. Build the core library
cd core
npm run build        # compiles TypeScript → dist/

# 2. Build the GUI
cd ../gui
npm run build        # Vite build → dist/

# 3. Open the VS Code extension in VS Code
cd ../extensions/vscode
npm run build        # bundles extension

# 4. Press F5 in VS Code to launch the Extension Development Host
```

Alternatively, use the VS Code launch configurations found in `.vscode/launch.json`.

### Running Tests

```bash
# Core unit tests (Jest)
cd core
npm test

# GUI tests (Vitest)
cd gui
npm test

# VS Code extension tests
cd extensions/vscode
npm test
```

### Building the Binary Server

```bash
cd binary
npm install
npm run build    # bundles a standalone Node.js server via pkg
```

---

## 3. Project Structure

```
continue/
├── core/                        # IDE-agnostic engine (TypeScript)
│   ├── src/
│   │   ├── autocomplete/        # Inline autocomplete logic
│   │   ├── config/              # Config loading & validation
│   │   ├── context/             # Context providers & retrieval
│   │   │   ├── providers/       # Built-in context providers
│   │   │   └── retrieval/       # Codebase retrieval / RAG
│   │   ├── indexing/            # Codebase indexer (embeddings, chunking)
│   │   ├── llm/                 # LLM abstraction layer
│   │   │   └── llms/            # Concrete provider implementations
│   │   ├── protocol/            # IDE ↔ Core message protocol types
│   │   ├── slash/               # Slash command definitions
│   │   ├── util/                # Shared utilities
│   │   ├── core.ts              # Main Core class — wires everything together
│   │   └── index.ts             # Public exports
│   ├── package.json
│   └── tsconfig.json
│
├── gui/                         # React webview UI
│   ├── src/
│   │   ├── components/          # Reusable React components
│   │   ├── pages/               # Top-level page views
│   │   ├── redux/               # Redux state management
│   │   ├── hooks/               # Custom React hooks
│   │   └── App.tsx              # App entry point
│   ├── package.json
│   └── vite.config.ts
│
├── extensions/
│   ├── vscode/                  # VS Code extension
│   │   ├── src/
│   │   │   ├── extension.ts     # Extension activation entry point
│   │   │   ├── commands.ts      # VS Code command registrations
│   │   │   ├── continueIdeClient.ts  # Bridges Core ↔ VS Code APIs
│   │   │   └── webviewProtocol.ts    # GUI ↔ extension message handling
│   │   └── package.json         # Extension manifest (contributes, commands, etc.)
│   │
│   └── jetbrains/               # JetBrains (IntelliJ/PyCharm/etc.) plugin
│       ├── src/main/kotlin/…    # Kotlin plugin source
│       └── build.gradle.kts
│
├── binary/                      # Standalone binary/server wrapper
│   └── src/                     # Bundles core as a standalone process
│
├── docs/                        # Docusaurus documentation site
│   └── docs/
│
└── .github/                     # CI/CD workflows and issue templates
```

### Key Files

| File | Purpose |
|---|---|
| `core/src/core.ts` | Central `Core` class — entry point for all IDE interactions |
| `core/src/index.ts` | Public API surface of the core package |
| `core/src/config/ConfigHandler.ts` | Loads and watches `~/.continue/config.json` |
| `core/src/llm/llms/index.ts` | Registry of all supported LLM providers |
| `core/src/protocol/ide.ts` | TypeScript types for IDE ↔ Core protocol messages |
| `core/src/autocomplete/completionProvider.ts` | Autocomplete engine |
| `core/src/indexing/CodebaseIndexer.ts` | Drives codebase embedding & indexing |
| `extensions/vscode/src/extension.ts` | VS Code extension activation |
| `extensions/vscode/src/continueIdeClient.ts` | VS Code implementation of the IDE client |
| `gui/src/App.tsx` | React webview application root |

### Important Configuration Files

| File | Purpose |
|---|---|
| `~/.continue/config.json` | **User's** runtime configuration (models, context providers, etc.) |
| `extensions/vscode/package.json` | VS Code extension manifest — commands, keybindings, settings |
| `core/tsconfig.json` | TypeScript config for core |
| `gui/vite.config.ts` | Vite bundler config for the webview |
| `extensions/jetbrains/build.gradle.kts` | Gradle build for JetBrains plugin |

---

## 4. Development Workflow

### Coding Standards

- **Language:** TypeScript (strict mode) for all JS/TS code; Kotlin for JetBrains.
- **Formatting:** Prettier (check `extensions/vscode/.prettierrc` or root config).
- **Linting:** ESLint — run `npm run lint` in the relevant package.
- **Naming:** camelCase for variables/functions, PascalCase for classes/types/interfaces, UPPER_SNAKE_CASE for constants.
- **Imports:** Absolute imports from package roots preferred over deep relative paths.

### Making Changes

1. **Core logic** → edit files under `core/src/`, rebuild with `npm run build` in `core/`.
2. **UI changes** → edit files under `gui/src/`, the Vite dev server hot-reloads automatically.
3. **VS Code commands/settings** → edit `extensions/vscode/src/commands.ts` and `package.json`.
4. **New LLM provider** → add a class in `core/src/llm/llms/` extending `BaseLLM`, then register it in `core/src/llm/llms/index.ts`.
5. **New context provider** → add a class in `core/src/context/providers/` implementing `IContextProvider`, register in the provider index.
6. **New slash command** → add to `core/src/slash/`.

### Testing Approach

- **Unit tests** live alongside source files in `__tests__/` subdirectories (Jest for core).
- **GUI tests** use Vitest.
- Integration/E2E tests: **[VERIFY]** check `.github/workflows/` for CI test stages.
- When adding a new LLM or context provider, add corresponding unit tests.

### Build & Deployment

```
core  →  npm run build   (tsc)
gui   →  npm run build   (vite build)
vscode extension → npm run build / vsce package
jetbrains plugin → ./gradlew buildPlugin
binary → npm run build (pkg)
```

CI/CD is managed via **GitHub Actions** (`.github/workflows/`).

### Contribution Guidelines

1. Fork the repo, create a feature branch: `git checkout -b feat/my-feature`.
2. Follow coding standards above.
3. Write/update tests for your changes.
4. Run `npm test` in all affected packages before pushing.
5. Open a Pull Request against `main` — see `CONTRIBUTING.md` for full details.
6. Reference related issues in your PR description.

---

## 5. Key Concepts

### LLM Abstraction Layer

`core/src/llm/` provides a unified interface (`BaseLLM`) over many LLM providers. Each provider (OpenAI, Anthropic, Ollama, Azure, Gemini, etc.) lives in `core/src/llm/llms/` and implements:
- `_streamComplete()` / `_streamChat()` — the core generation methods
- Model listing, token counting, context-window metadata

### Context Providers

Context providers are plugins that supply relevant information to the LLM prompt. They implement `IContextProvider` and are invoked when a user types `@provider-name` in the chat. Built-in providers include `FileContextProvider`, `CodeContextProvider`, `GitDiffContextProvider`, `TerminalContextProvider`, etc.

### Codebase Indexing & Retrieval

`core/src/indexing/CodebaseIndexer.ts` chunks source files and stores embeddings in a local vector store. `core/src/context/retrieval/` implements retrieval-augmented generation (RAG) to find relevant code snippets for a query.

### IDE Protocol

The Core and IDE extensions communicate through a typed message-passing protocol defined in `core/src/protocol/`. This allows the same Core to work with both VS Code and JetBrains without IDE-specific code leaking into the engine.

### Config System

User configuration lives in `~/.continue/config.json` (or `config.ts` for advanced users). `ConfigHandler` watches this file and hot-reloads the running extension when changes are detected. Config covers: models, context providers, slash commands, UI preferences, and provider API keys.

### Slash Commands

Custom commands invoked with `/command-name` in chat. Built-in ones (e.g., `/edit`, `/comment`, `/test`) live in `core/src/slash/`. Users can define custom slash commands in their `config.json`.

### Webview Protocol

The React GUI (running inside a VS Code webview or JetBrains JCEF panel) communicates with the extension host through `webviewProtocol.ts`. Messages are strongly typed and flow bidirectionally.

---

## 6. Common Tasks

### Add a New LLM Provider

1. Create `core/src/llm/llms/MyProvider.ts` extending `BaseLLM`.
2. Implement `_streamChat()` and optionally `_streamComplete()`, `listModels()`.
3. Register the class in `core/src/llm/llms/index.ts`.
4. Add documentation in `docs/docs/reference/model-providers/`.
5. Write unit tests in `core/src/llm/llms/__tests__/`.

### Add a New Context Provider

1. Create `core/src/context/providers/MyProvider.ts` implementing `IContextProvider`.
2. Export and register it in the providers index.
3. Test it by typing `@MyProvider` in the Continue chat panel.

### Add a New VS Code Command

1. Define the command in `extensions/vscode/package.json` under `contributes.commands`.
2. Add the handler in `extensions/vscode/src/commands.ts`.
3. Register keybindings in `contributes.keybindings` if needed.

### Update the User-Facing Docs

```bash
cd docs
npm install
npm start    # starts Docusaurus dev server at http://localhost:3000
```

Edit Markdown files in `docs/docs/`.

### Debug the Extension

1. Open `extensions/vscode/` in VS Code.
2. Press **F5** → launches Extension Development Host.
3. Set breakpoints in `extension.ts` or `continueIdeClient.ts`.
4. Use `console.log` / `Output → Continue` channel for runtime logs.
5. For GUI debugging, open the webview DevTools: **Help → Toggle Developer Tools** in the Extension Host window.

### Build & Test the JetBrains Plugin

```bash
cd extensions/jetbrains
./gradlew runIde          # launches a sandboxed IDE with the plugin
./gradlew test            # runs plugin tests
./gradlew buildPlugin     # produces a .zip distributable
```

---

## 7. Troubleshooting

### Extension doesn't activate
- Check the **Output → Continue** panel in VS Code for error messages.
- Ensure `core` and `gui` have been built (`npm run build` in each).
- Verify Node.js version is ≥ 20.

### Autocomplete not triggering
- Confirm `"continue.enableTabAutocomplete": true` in VS Code settings.
- Check that a model is configured and reachable in `~/.continue/config.json`.
- Look for network errors in the Output panel.

### Config changes not taking effect
- `ConfigHandler` watches `~/.continue/config.json` — edits should hot-reload.
- If they don't, reload the VS Code window (`Ctrl+Shift+P` → *Reload Window*).

### LLM provider errors (timeouts, auth)
- Verify API keys in `~/.continue/config.json`.
- For Ollama, ensure the local server is running (`ollama serve`).
- Check provider-specific docs in `docs/docs/reference/model-providers/`.

### JetBrains: binary server not starting
- Check that Java 17+ is installed and `JAVA_HOME` is set.
- Run `./gradlew runIde` to see full Gradle/IDE logs.

### Type errors after pulling new changes
- Re-run `npm install` in `core/`, `gui/`, and `extensions/vscode/`.
- Re-run `npm run build` in `core/` — the extension depends on `core/dist/`.

### Tests failing locally
- Delete `node_modules` and reinstall: `rm -rf node_modules && npm install`.
- Ensure you're on the correct Node.js version (`node --version`).

---

## 8. References

### Official Resources
- **Docs site:** https://docs.continue.dev
- **Website:** https://continue.dev
- **GitHub:** https://github.com/continuedev/continue
- **Discord community:** https://discord.gg/vapESyrFmJ

### Key Documentation Pages
- [Configuration reference](https://docs.continue.dev/reference/config) — all `config.json` options
- [Model providers](https://docs.continue.dev/reference/model-providers/openai) — per-provider setup guides
- [Context providers](https://docs.continue.dev/customization/context-providers) — built-in and custom context
- [Slash commands](https://docs.continue.dev/customization/slash-commands) — built-in and custom commands
- [Creating your own extension](https://docs.continue.dev/advanced/create-extension) — SDK docs

### Internal Architecture Docs
- `core/src/protocol/ide.ts` — canonical source of truth for IDE ↔ Core messages
- `core/src/index.ts` — public API surface of the core package
- `CONTRIBUTING.md` — contribution process and code-of-conduct

### Related Technologies
- [VS Code Extension API](https://code.visualstudio.com/api)
- [IntelliJ Platform SDK](https://plugins.jetbrains.com/docs/intellij/welcome.html)
- [LangChain.js](https://js.langchain.com) — some retrieval primitives may use it
- [Docusaurus](https://docusaurus.io) — docs site framework

---

> **Note:** Sections marked `[VERIFY]` contain reasonable assumptions that should be confirmed against the actual codebase. Please update this file as the project evolves.
>
> **Tip:** You can create additional `rules.md` files in subdirectories (e.g., `core/.continue/rules/rules.md`) for component-specific guidance that Continue will automatically load when you're working in those directories.