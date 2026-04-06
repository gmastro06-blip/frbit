# Tibia Controls Fit Matrix

Source reviewed on 2026-04-05:

- [Tibia controls manual](https://www.tibia.com/gameguides/?subtopic=manual&section=control)

Purpose:

- map the official Tibia controls taxonomy to the current input and interaction stack
- separate strong existing low-level input support from missing higher-level client actions
- identify where the repo still models client controls as local offsets, raw VKs or one-off flows

## Executive Summary

The controls manual is useful for this project, but mainly as an action taxonomy, not as an implementation guide.

What it confirms:

- the Tibia client exposes a stable set of first-class player actions: movement, looking and inspecting, moving items, pushing creatures, using items, rotating items, combat, looting, container sorting and container management
- those actions are not only raw clicks and key presses, they are semantic client operations with visible outcomes
- keyboard shortcuts are an official part of the client interaction model, not just a convenience layer

What the repo already does well:

- raw keyboard and mouse input is strong and flexible in `src/input_controller.py`
- quick loot already exists both as a context-menu action and as the native `Alt+Q` hotkey in `src/looter.py`
- some interaction verification already exists in `src/action_verifier.py`
- the script engine already handles a limited `use with crosshair` flow

Main implication for the repo:

- the main gap is not missing low-level input primitives
- the main gap is missing reusable semantic actions above those primitives
- current behavior is spread across looter, depot, trade, script and safety flows, which makes control logic harder to verify and reuse

## Matrix

| Control area | Manual relevance | Current repo coverage | Fit for project | Main gap | Priority |
| --- | --- | --- | --- | --- | --- |
| Character movement | High. Core client action. | `src/input_controller.py`, `src/navigator.py`, `src/script_executor_walk.py` | High | Movement keys and diagonal stepping are implemented, but movement-stop and movement-state semantics are not modeled as explicit client actions. | P2 |
| Stop or cancel current action | High. Practical client control even when not highlighted as its own system. | Local `Esc` usage exists in `src/script_executor_runtime.py`, `src/session_safety.py`, `src/trade_manager.py`, configurable close-container hotkeys in `src/depot_manager.py` | High | There is no single semantic action like `cancel_current_action()` or `close_open_ui()` with verification. | P1 |
| Looking around and inspecting | High. Official first-class interaction category. | Partial only. Context-menu support exists in `src/ui_detection.py`, and `src/looter.py` comments already encode menu order including `Look`. | Medium-high | No general `look` or `inspect` action, no verifier for tooltip or inspect result, no reusable object-inspection flow. | P2 |
| Moving items | High. Official first-class interaction category. | Very weak. The repo mostly uses `shift_click` and menu-based deposit flows in `src/input_controller.py`, `src/looter.py`, `src/depot_manager.py`. | Medium-high | No drag-and-drop primitive, no stack-splitting abstraction, no verified item relocation flow. | P1 |
| Pushing creatures | Medium-high. Relevant to blockage recovery and navigation correctness. | Indirect only through walking and stuck handling in `src/stuck_detector.py` and navigation modules. | Medium | No dedicated push action or push verification. Current recovery is movement-centric, not interaction-centric. | P3 |
| Using items | High. Official first-class interaction category. | Partial. Hotkey usage is widespread, and `use with crosshair` exists in `src/script_executor.py`. | High | Item-use semantics are fragmented across scripts and modules. No reusable `use_hotkey_on_tile`, `use_item_on_target`, or `use_item_with_crosshair` action layer. | P1 |
| Rotating items | Low-medium. Real client control, but niche for the current scope. | None found. | Low | No primitive or use case in current automation goals. | P4 |
| Combat controls | High. Official first-class interaction category. | Strong hotkey sending exists in `src/combat_manager.py`, `src/combat_manager_helpers.py`, `src/healer.py`, `src/condition_monitor.py`. | High | Combat actions are sent as raw VKs with limited client-state verification. No explicit layer for attack, flee, target-cycle, or safe cancel semantics. | P2 |
| Looting | High. Official first-class interaction category. | Strongest match: `src/looter.py`, `src/looter_runtime.py`, `src/ui_detection.py` | High | Quick loot is well covered, but open-loot flows still depend on context-menu offsets and container assumptions. | P1 |
| Sorting containers | Medium. Official client container action. | None beyond implicit slot iteration and right-click menu handling in `src/looter.py` and `src/depot_manager.py`. | Low-medium | No reasoned support for sorting state, and no need yet unless container reading becomes richer. | P4 |
| Manage containers | High. Official client container workflow. | Partial: open, detect, close, and `Stow All` exist in `src/depot_manager.py`, `src/depot_manager_runtime.py`, `src/looter.py`, `src/ui_detection.py`. | High | Management actions are fragmented and still rely on menu entry indexes, fallback offsets, and right-side container assumptions. | P1 |
| Keyboard shortcuts | High. Official client interaction layer. | Broad coverage across `src/looter.py`, `src/anti_kick.py`, `src/gm_detector.py`, `src/chat_responder.py`, `src/trade_manager.py`, `src/script_executor_runtime.py` | High | Hotkeys are scattered and encoded locally. No central registry of semantic meaning, fallback strategy or post-action verification. | P1 |

## Key Findings By Module

### 1. Raw input is already ahead of the control model

`src/input_controller.py` already supports:

- single key presses
- modifier combos
- dual-key movement
- hover without click
- left, right and shift-click
- multiple backends and hardware failover

That means the repo does not need another low-level input rewrite.

What it needs is a thin semantic layer that turns those primitives into official client actions.

### 2. Quick Loot proves the semantic-action direction is correct

`src/looter.py` already models one official client action in two ways:

- menu-driven `Quick Loot`
- native `Alt+Q` quick loot

That is exactly the pattern worth generalising:

- express the user intent first
- choose the best control path second
- verify the visible outcome third

### 3. `use with crosshair` is implemented, but isolated

`src/script_executor.py` already includes a targeted helper for rope or shovel flows that:

- sends a hotkey
- clicks the character tile to complete the crosshair action

This is valuable because it shows the repo already needs client-action semantics, but only in isolated script paths.

That behavior should be promoted into a reusable interaction primitive rather than kept as a script-local special case.

### 4. Container management is still too local and too positional

The container toolchain is useful but brittle:

- `src/ui_detection.py` defaults container search to the right third of the screen
- `src/depot_manager_runtime.py` depends on menu entry indexes for `Stow All`
- `src/looter_runtime.py` still falls back to hard-coded vertical offsets when visual menu parsing fails

This matches the control manual's warning indirectly: container operations are first-class client actions, but the repo still treats them as a handful of local pointer tricks.

### 5. Action verification exists, but is underused outside NPC dialogs and movement

`src/action_verifier.py` already has useful building blocks:

- retry wrappers
- frame-validity checks
- screen-based confirmation helpers
- blue-keyword dialog interaction

Those are the right ingredients for a stronger control layer.

The missing part is systematic use after control actions such as:

- container opened
- container closed
- loot transferred
- context menu appeared
- inspect dialog or tooltip appeared
- crosshair use completed

## Prioritized Backlog

### P1 - Introduce a client-action layer above `InputController`

Candidate actions worth centralising first:

- `quick_loot_target()`
- `open_context_entry()`
- `use_hotkey_on_tile()`
- `cancel_current_action()`
- `close_open_containers()`
- `stow_all_container()`

Why this matters:

- these actions already exist in fragments across the repo
- centralising them reduces duplicated timing, VK and fallback logic
- verification can be attached once instead of reimplemented per subsystem

### P1 - Add verification hooks for interaction outcomes

Examples:

- verify a container appeared after `Open`
- verify a container disappeared after close or stow
- verify loot count or container occupancy changed after looting
- verify context menu visibility before selecting an entry

Preferred direction:

- reuse `src/action_verifier.py` patterns instead of adding more sleeps

### P1 - Add drag-and-drop as a first-class primitive

Needed because:

- the manual treats moving items as a real client action
- current flows over-rely on `shift_click`, quick loot or menu actions
- more advanced depot, inventory and resupply behavior will eventually need true item relocation

Minimum scope:

- drag from source slot to destination slot
- optional modifier support if Tibia behavior depends on it
- visible verification that source or destination changed

### P1 - Centralise hotkey semantics

Goal:

- stop scattering raw VK meanings across looter, combat, trade, GM safety and chat modules

Desired outcome:

- one place that knows which hotkeys mean attack, quick loot, escape, close containers, reply, camera rotate or scripted item use
- cleaner fallback when a preferred shortcut is unavailable

### P2 - Add inspect and look primitives only if they unlock verification

Use cases that would justify them:

- verifying an object or corpse identity
- debugging wrong target selection
- safer scripted interactions where opening or using is ambiguous

If they do not improve decisions, they should remain low priority.

### P2 - Model stop and cancel semantics explicitly

Examples:

- abort current crosshair use
- close transient dialogs before resuming route logic
- cancel failed trade or NPC flows cleanly

This is more valuable than adding rare manual controls because it directly reduces state drift between subsystems.

### P3 - Evaluate push-creature support only for stuck recovery

Reason:

- it is a real manual action, but it only matters if it improves blocked-tile recovery beyond current movement retries

### P4 - Defer rotate-item and container-sorting support

Reason:

- both are official controls, but neither currently changes the bot's route execution, combat safety or supply economy enough to justify implementation now

## What Not To Prioritize

These are real control surfaces but weak ROI for the current project scope:

- outfit, mount and familiar customization
- rotate-item handling for decorative or niche puzzle interactions
- general container sorting features unless they improve a concrete depot or looting decision
- look or inspect support that does not feed any verifier or decision engine

## Recommended Next Step

If work starts from this document, the best next technical step is:

1. create a small `client_actions` module that extracts three already-proven actions from current code: quick loot, crosshair item use, and close or cancel UI state
2. route those actions through a shared verification path instead of per-module sleeps and offsets
3. use that layer first in looter, depot and script execution before expanding it to rarer controls

That would convert the manual's main value, the official control taxonomy, into a cleaner and more reliable runtime architecture.
