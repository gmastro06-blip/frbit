# Tibia Interface Fit Matrix

Source reviewed on 2026-04-05:

- [Tibia interface manual](https://www.tibia.com/gameguides/?subtopic=manual&section=interface)

Purpose:

- map the official Tibia client interface to the current vision stack
- separate what is directly useful for waypoint-navigator from what is only domain context
- identify where the codebase still assumes a stable layout that the official client does not guarantee

## Executive Summary

The interface manual is useful for this project, but mainly as a UI contract, not as an operational dataset.

What it confirms:

- core widgets such as minimap, status bars, inventory, battle list, console, cooldown bar and action bars are real, stable concepts in the client
- many of those widgets are movable, resizable, hideable or split into sidebars
- some battle-facing widgets have filters, secondary lists or alternative states that can change what the bot sees

What it does not provide:

- no ROI values we can trust in a live setup
- no templates, icon atlases or calibrated screenshots
- no guarantee that a user keeps the default layout

Main implication for the repo:

- any detector that depends on a fixed ROI is a controlled shortcut, not a robust interface model
- adaptive anchors and calibration are the right long-term direction

## Matrix

| Interface element | Manual relevance | Current repo coverage | Fit for project | Main gap | Priority |
| --- | --- | --- | --- | --- | --- |
| Minimap | High. Core navigation surface with zoom, floor selector and marks. | `src/minimap_radar.py`, `src/minimap_calibrator.py`, `src/position_resolver.py`, `src/action_verifier.py`, `src/adaptive_roi.py` | High | The manual confirms zoom, floors and overlays can vary. Radar is core, but still depends on a stable minimap widget region and known presentation. | P1 |
| HP/MP status bars | High. Manual confirms bars may be shown in sidebars, around the game window, both, or hidden. | `src/hpmp_detector.py`, `src/healer.py`, `src/healer_runtime.py`, `src/adaptive_roi.py` | High | Detector logic is useful, but layout variability is larger than the current default ROI assumptions. | P1 |
| Condition indicator | High. Manual explicitly places conditions in the inventory area and names logout block as one of them. | `src/condition_monitor.py`, `src/healer_runtime.py`, `src/monitor_gui.py` | High | Current condition support is partial. Color ranges cover poison, paralyze, burning, drunk, bleeding and freezing only. Manual implies a broader set. | P1 |
| Battle list | High. Manual confirms HP bars, skulls, sorting, filtering and secondary battle lists. | `src/combat_manager.py`, `src/pvp_detector.py`, `src/gm_detector.py`, `src/calibrator.py`, `src/adaptive_roi.py` | High | Current detectors assume one ROI and a reasonably standard list shape. Secondary lists, filtering and sorting can change what is visible. | P1 |
| Containers and sidebars | Medium-high. Manual confirms windows and containers can move across sidebars and positions are persistent. | `src/ui_detection.py`, `src/depot_manager.py`, `src/depot_manager_runtime.py` | High | Container detection is heuristic and biased toward the right side of the screen. Sidebars make this fragile. | P1 |
| Inventory panel and body slots | Medium. Manual confirms slot semantics and indicator fields below the portrait. | `src/inventory_manager.py`, `src/death_handler.py` | Medium | The repo monitors inventory fullness and supplies, but does not model the visible slot layout deeply. No reader for the blessing indicator, soul indicator or capacity indicator. | P2 |
| Blessing indicator and dialog | Medium. Relevant to death-risk decisions. | No dedicated visual reader. Death handling exists in `src/death_handler.py`. PvP/skull logic exists in `src/pvp_detector.py`. | Medium | The bot can recover after death, but it cannot visually reason about blessing state from the interface. | P3 |
| Soul and capacity indicators | Medium. Inventory area exposes both. | No dedicated readers. `src/inventory_manager.py` tracks slot fill, not the capacity number shown by the client. | Low-medium | Useful for richer state estimation, but not critical for current route execution. | P3 |
| Cooldown bar | Medium-high. Manual confirms visible spell and spell-group cooldown feedback. | Internal cooldown bookkeeping exists in `src/combat_manager.py` and spell metadata in `src/game_data.py`, but no visual cooldown reader. | Medium | The bot knows configured cooldowns, not real client cooldown state. No screen-level verification. | P2 |
| Action bars | Medium. Manual confirms spells, items, multi-actions and hotkey bindings are visible and configurable. | The bot sends configured Vks and hotkeys, but does not read action bars visually. | Medium | No validation that an action bar slot is present, locked, assigned or on cooldown. | P3 |
| Combat controls and secure mode | Medium. Manual defines combat stance, chase mode, expert mode and secure mode. | No dedicated reader. Combat logic is handled procedurally in `src/combat_manager.py`. | Medium | The bot does not verify what combat mode the client is currently in. This is mostly a safety and PvP correctness gap. | P3 |
| Console and chat tabs | Medium. Manual confirms chat tab duplication, ignore state and visible communication surface. | `src/chat_responder.py` exists, but there is no strong general visual parser for the console described in the manual. | Medium | Chat/NPC interaction remains weakly coupled to the visible console state. | P3 |
| Party list | Low-medium. Manual confirms HP, mana and skull visibility for party members. | No dedicated party list detector. | Low | Could help coordinated support logic later, but not necessary for solo navigation. | P4 |
| Cyclopedia, analytics, quest widgets, reward wall, social, highscores | Low | No direct runtime use. | Low | Mostly irrelevant for automation safety and route execution. | P4 |

## Key Findings By Module

### 1. Adaptive ROI is aligned with the manual

The strongest architectural match is `src/adaptive_roi.py`.

Why:

- the manual explicitly says major widgets can be moved or resized
- `adaptive_roi.py` already treats HP, MP, minimap, battle list and condition icons as screen elements that should be located, not assumed

Consequence:

- this module should become the default source of truth for critical UI ROIs
- fixed JSON ROIs should be treated as fallback or bootstrap values

### 2. Condition handling has a semantic mismatch today

`src/healer_runtime.py` expects condition-driven behavior for things like `haste` and `battle`, but `src/condition_monitor.py` color detection currently covers only a narrow subset.

That means:

- the manual says the interface exposes more states than we currently read
- the runtime already wants richer condition information than the detector reliably produces

Consequence:

- condition coverage should be expanded before adding more buff-aware logic

### 3. Battle list support is useful but over-assumes layout

The manual adds three practical warnings:

- battle lists can be filtered
- battle lists can be sorted differently
- premium users can open secondary battle lists

Current implication:

- a detector may succeed technically while still reading the wrong list or an incomplete list
- combat and PvP safety can silently degrade without a hard failure

### 4. Inventory support is not the same as interface understanding

`src/inventory_manager.py` is good at supply and fill estimation, but it is not a general reader of the inventory interface described by the manual.

What is missing:

- blessing indicator interpretation
- soul point reading
- capacity reading from the dedicated UI field
- richer understanding of slot state and equipment presentation

## Prioritized Backlog

### P1 - Make critical vision layout-aware by default

Target modules:

- `src/hpmp_detector.py`
- `src/condition_monitor.py`
- `src/combat_manager.py`
- `src/pvp_detector.py`
- `src/ui_detection.py`

Required direction:

- route all critical ROIs through adaptive anchors or calibration
- treat hard-coded ROIs as fallback only
- fail loudly when a required widget cannot be located

### P1 - Harden battle list semantics

Goals:

- detect battle list header or structural anchors, not only a rectangle
- detect whether the list appears filtered or sorted in a surprising way
- optionally support selecting which list is authoritative when multiple battle lists are visible

### P1 - Expand condition coverage

Minimum additions worth evaluating:

- logout block
- haste
- battle sign if visually stable enough
- protection-zone related states only if they are visually reliable and actionable

Reason:

- these conditions change bot safety behavior more than purely cosmetic states

### P2 - Add visual cooldown verification

Goal:

- compare configured spell cooldown assumptions against what the client actually shows in the cooldown bar

Why this matters:

- current combat logic trusts config and timing
- the client exposes stronger truth than our local timer assumptions

### P2 - Improve container and sidebar robustness

Goal:

- detect container windows based on anchors or title/body structure anywhere on screen
- remove the implicit assumption that containers live in the right third

### P3 - Add readers only where they change decisions

Candidates:

- blessing indicator
- soul indicator
- capacity indicator
- combat mode and secure mode

Rule:

- only implement these if they affect routing, death policy, buying logic or risk management
- do not build readers just because the manual mentions the widget

## What Not To Prioritize

The following manual sections are real interface features but have weak value for the current bot scope:

- Cyclopedia and analytics widgets
- social dialog and friend systems
- quest tracker and compendium widgets
- highscores and client help
- most website-linked or store-linked surfaces

These are low ROI unless the product scope moves toward full client analytics or account tooling.

## Recommended Next Step

If work starts from this document, the best next technical step is:

1. create a small interface-sanity pipeline that validates minimap, HP/MP, battle list, condition area and at least one container anchor before a live session starts
2. wire its output into the existing smoke and preflight path
3. block live execution when a required critical widget cannot be located confidently

That would turn the manual's biggest warning, movable UI, into an explicit preflight concern instead of a silent runtime failure mode.
