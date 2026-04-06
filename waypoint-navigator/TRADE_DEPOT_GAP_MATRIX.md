# Trade And Depot Client Fit Matrix

Source reviewed on 2026-04-05:

- Tibia interface manual: https://www.tibia.com/gameguides/?subtopic=manual&section=interface
- Tibia controls manual: https://www.tibia.com/gameguides/?subtopic=manual&section=controls
- Current implementation: src/trade_manager.py, src/depot_manager.py, src/depot_manager_runtime.py, src/script_executor_trade.py
- Current calibration: trade_config.json, depot_config.json
- Current tests: tests/test_trade.py

Purpose:

- determine how the latest Tibia client models trade and depot or storage workflows
- compare those expectations against the current repo behavior
- separate areas that already match the modern client from areas that still model older chest and container flows

## Executive Summary

The trade side is in relatively good shape.

The current TradeManager already assumes a modern NPC trade window with:

- dedicated buy and sell tabs
- a search field
- a quantity field
- an explicit action button flow
- an optional balance or bank-gold checkbox

That means the repo is not stuck on old chat-only trade semantics. It already speaks the language of the current client.

The depot side is different.

The current DepotManager does not model the modern storage surface as a first-class UI. It still treats depot work mainly as:

- opening a chest on the map
- waiting for a generic container window
- shift-clicking backpack slots
- or using context-menu based Stow All actions as a shortcut

That is useful, but it is not the same thing as supporting the full storage model of the latest client, which distinguishes inventory, depot, stash, inbox and Store inbox, and which exposes stronger container-management features.

Main conclusion:

- trade is mostly aligned with the modern client UI
- depot is only partially aligned and still behaves like a legacy chest or container automation layer
- the biggest missing concept is not input, but storage state

## Official Client Facts That Matter Here

### 1. Windows and containers are movable and restorable

The interface manual confirms that sidebars can be opened freely, windows and containers can be moved between them, and the client restores their last positions on relog.

Implication for this repo:

- fixed right-side assumptions are always fragile
- ROI and click positions can only be considered calibration data, not stable truth

### 2. Store Inbox is a separate storage surface

The interface manual states that Store purchases are delivered to the Store inbox, which can be opened anywhere. This is not the same thing as backpack, depot, stash or generic containers.

Implication for this repo:

- any resupply or item-routing model that only understands backpack plus depot is incomplete

### 3. The client distinguishes inventory, depot, stash, inbox and Store inbox

The interface manual says the Cyclopedia item summary can report items in inventory, depot, stash, inbox and Store inbox.

Implication for this repo:

- the latest client exposes multiple storage states
- current depot automation only works on the visible container subset of that model

### 4. Sorting Containers is now a first-class container feature

The controls manual confirms that containers can be sorted by:

- name
- weight
- expiry
- stack size

It also confirms options for:

- sorting containers first
- sorting nested containers
- manual sort mode

Implication for this repo:

- container layout is no longer just “whatever order items were inserted in”
- any bot flow that depends on slot order should assume the user can change it

### 5. Manage Containers is now a first-class workflow

The controls manual confirms that Manage Containers lets Premium players:

- assign containers to loot categories
- define fallback behavior
- route looted items automatically
- route items obtained in other ways, including NPC purchases and stash or depot retrieval, into specific containers when configured

Implication for this repo:

- the client itself already owns part of the item-routing problem
- the bot should either depend on that model explicitly or verify that the client configuration matches its expectations

### 6. Context menus still matter

The controls manual still confirms that Tibia uses context menus heavily for semantic actions.

Implication for this repo:

- the recent client-actions refactor direction is correct
- but menu index assumptions remain brittle if not verified visually

## Fit Matrix

| Surface | Latest-client behavior | Current repo coverage | Fit | Main gap | Priority |
| --- | --- | --- | --- | --- | --- |
| NPC trade window | Modern windowed flow with buy/sell tabs, search and explicit item selection. | Strong in src/trade_manager.py. The module already models buy and sell tabs, search field, quantity input and action buttons. | High | Layout is still mostly calibration-based and not strongly verified from visible structure. | P1 |
| Trade search flow | Item search is part of modern client ergonomics. | Strong. use_search_field, search_field_pos and first_item_pos exist in trade_config.json and code paths are covered in tests/test_trade.py. | High | Search success is only lightly verified. No deeper UI-state confirmation after filtering. | P2 |
| Trade quantity and confirmation | Modern trade uses amount entry and explicit confirmation. | Strong. qty_field_roi, _set_quantity(), buy_btn_pos, sell_btn_pos and ok_btn_pos are implemented. | High | No post-action verification that the transaction actually changed inventory or gold. | P1 |
| Trade bank-gold toggle | Current client supports paying from balance in supported trade flows. | Partial-strong. use_balance and balance_checkbox_pos exist and are wired in src/trade_manager.py. | Medium-high | Checkbox state is not read back visually; code assumes click equals desired state. | P2 |
| Trade fallback path | If GUI trade is unavailable, the bot can still talk to NPCs by chat. | Present in src/script_executor_trade.py. | Medium | Chat fallback bypasses modern GUI semantics entirely and cannot use search, price reading or balance state. | P2 |
| Face-to-face trade with players | Separate trade surface in the modern client. | None found. | Low | Not currently needed for hunt and resupply workflows. | P4 |
| Market window | Separate economic surface in the modern client. | None found. | Low | Out of scope for current depot and hunt loops. | P4 |
| Depot chest interaction | Classical map-object entry point into depot storage. | Strong. src/depot_manager.py computes the chest tile, opens the context menu and selects Open. | Medium | This is only the entry point, not the full storage model of the modern client. | P2 |
| Generic open-container detection | After opening the chest, the client shows containers in sidebars. | Partial. src/depot_manager_runtime.py waits for a visible container by ROI and color heuristics. | Medium | Detection is generic, not semantic. It does not distinguish depot locker, stash, inbox or backpack. | P1 |
| Deposit by moving visible items | Legacy-compatible flow based on backpack slots and visible containers. | Strong. shift-click and loot_all style deposit modes exist. | Medium | Works only on the visible container layer, not on the broader storage model. | P2 |
| Stow All from context menus | Modern client offers Stow All style actions for faster deposit. | Partial-strong. deposit_mode=stow_all exists, and runtime iterates item menus to trigger stow. | Medium-high | The code still depends on menu entry indexes, right-panel slot assumptions and repeated per-item context clicks. | P1 |
| Manage Containers | Client-managed routing of loot and obtained items into configured containers. | Weak indirect coverage only. Depot and looter can benefit from it, but no module models, verifies or configures it. | Low-medium | The repo does not know whether the user's container categories, fallback rules or obtain-column settings match the automation assumptions. | P1 |
| Container sorting | Containers can be sorted and manually arranged by the client. | Essentially none. Current code assumes slot order is operationally meaningful. | Low | Sorting state can silently break slot-based assumptions in depot and looting. | P2 |
| Stash, inbox and Store inbox | Distinct storage surfaces in the current client. | None as first-class UI concepts. | Low | The repo has no storage abstraction above visible containers. | P1 |
| Depot or stash retrieval | Retrieved items can be routed by Manage Containers in the current client. | Very weak. bank_withdraw exists for gold, but no general stash or depot retrieval UI automation exists. | Low | No direct support for retrieving supplies from stash or depot windows. | P1 |

## What The Code Already Gets Right

### Trade is already modern-client aware

TradeManager is not a purely old-style NPC chat macro.

Evidence in the repo:

- src/trade_manager.py has buy_tab_pos and sell_tab_pos
- src/trade_manager.py has use_search_field, search_field_pos and first_item_pos
- src/trade_manager.py reads unit price by OCR
- src/trade_manager.py supports a balance checkbox
- trade_config.json is calibrated for a current GUI trade layout
- tests/test_trade.py covers the search-field path extensively

That is a meaningful foundation, not a placeholder.

### Depot already uses useful modern shortcuts

DepotManager is not limited to raw drag-and-drop. It already uses client-native shortcuts where possible.

Evidence in the repo:

- src/depot_manager.py supports deposit_mode="stow_all"
- src/depot_manager_runtime.py iterates context-menu based stow actions
- src/depot_manager.py now uses the shared close_open_containers action

That matters because it reduces the amount of brittle manual item movement.

## Where The Repo Is Still Modeling An Older World

### 1. Depot is still container-centric, not storage-centric

The repo mainly sees:

- a chest on the floor
- a container window on screen
- backpack slots
- right-click actions

The modern client exposes a broader storage model:

- depot
- stash
- inbox
- Store inbox
- client-managed routing rules

The code does not yet model that bigger picture.

### 2. Container position remains too positional

The manual confirms containers and windows can move between sidebars and return to previous positions automatically.

Current depot flows still rely on:

- container_roi
- backpack_slot_origin
- stow_panel_y_start
- stow_container_index

Those are practical calibration knobs, but they are not a semantic understanding of the storage UI.

### 3. Manage Containers is assumed, not integrated

The latest client can route loot and obtained items into predefined containers. The repo could benefit from that heavily, but it currently does not:

- read the active Manage Containers state
- verify that the main container fallback is configured sensibly
- verify whether NPC purchases will land where the automation expects
- reason about stash retrieval routing

### 4. Trade is good, but still too blind after clicks

TradeManager has the right visible controls, but its verification is still weak after the action is sent.

Examples:

- it does not verify that the selected tab really changed
- it does not verify that the balance checkbox ended in the desired state
- it does not verify that buy or sell changed the visible amount, inventory or price label

## Practical Interpretation For This Project

If the goal is safe and robust hunt resupply on the latest Tibia client, then the repo should treat trade and depot differently.

### Trade

Trade can remain GUI-driven.

The current implementation is already close to the correct mental model:

- detect the trade window
- select the right tab
- search the item
- set quantity
- confirm action

This path mainly needs stronger state verification, not a new architecture.

### Depot and storage

Depot should not remain a generic “open chest and click around” flow forever.

The next model should reason about:

- visible containers versus client storage surfaces
- which obtained items are expected to route automatically
- when stow is enough and when actual retrieval is required
- whether the client-side Manage Containers setup is compatible with the script

## Recommended Next Steps

### P1 - Add a first-class storage model

Introduce one semantic layer that distinguishes:

- main container
- loot containers
- depot or locker surface
- stash
- inbox
- Store inbox

This does not need full automation on day one. Even read-only detection would already reduce ambiguity.

### P1 - Add visual verification to TradeManager

Priority checks:

- active tab detection
- search result visibility confirmation
- balance checkbox state confirmation
- post-buy or post-sell confirmation signal

### P1 - Stop treating Manage Containers as invisible infrastructure

At minimum, add one preflight or calibration check that documents the expected client setup for:

- main container fallback
- accepted or skipped loot behavior
- obtain-column routing for purchased or retrieved items

### P2 - Make depot flows resilient to sorted containers

Any slot-iteration flow should assume that container sorting or manual sort mode may be active.

That means preferring:

- semantic item detection
- category-based routing assumptions
- or explicit disablement requirements for sort-sensitive flows

### P2 - Add a live validation checklist

The most valuable field test is simple:

- open NPC trade and confirm the calibrated buy, sell, search and balance positions
- open depot with the user's real sidebar layout
- verify whether items from NPC trade and stash retrieval land in the expected container
- verify whether stow_all still hits the intended menu entries

## Bottom Line

Trade is already reasonably adapted to the latest Tibia client.

Depot is not yet a true modern storage-window implementation. It is a useful hybrid of chest opening, visible container handling and Stow All shortcuts.

If only one area should be raised to current-client semantics next, it should be depot and storage management, not trade.