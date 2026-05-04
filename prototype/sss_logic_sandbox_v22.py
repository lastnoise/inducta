#!/usr/bin/env python3
"""
SSS Logic Sandbox v21

Change from v20:
- Adds a first-pass collapse/study state.
- Collapse triggers when the board is full and more enemy pressure is scheduled,
  or when the board is full with no eco engine left.
- Collapse does not auto-reset. The player can study the board, preview the next
  world shift, then reset by choice.

Run:
  python sss_logic_sandbox_v21.py

Recommended:
  pip install colorama
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple
import os
import random
import copy

try:
    from colorama import init
    init()
except Exception:
    pass


RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
ECO = "\033[95m"
ARB = "\033[94m"
LAT = "\033[92m"
EXEC = "\033[38;5;208m"
WARN = "\033[93m"
GOOD = "\033[96m"

TILE_WIDTH = 6


def color(text: str, ansi: str) -> str:
    return f"{ansi}{text}{RESET}"


def pad_token(text: str) -> str:
    return text.center(TILE_WIDTH)


@dataclass
class Cell:
    kind: str = "empty"
    value: int = 0
    hold: int = 0
    direction: int = 1       # Executors: 0 up, 1 right, 2 down, 3 left.
    target: str = ""         # Executors/Latchers: queued/load target tile.
    timer: int = 0           # Latchers: turns until queued attack resolves.


NUM_TO_POS = {
    "7": (0, 0), "8": (0, 1), "9": (0, 2),
    "4": (1, 0), "5": (1, 1), "6": (1, 2),
    "1": (2, 0), "2": (2, 1), "3": (2, 2),
}

POS_TO_NUM = {pos: n for n, pos in NUM_TO_POS.items()}
ROWS = [("7", "8", "9"), ("4", "5", "6"), ("1", "2", "3")]
SPAWN_KIND_ORDER = ("latcher", "arbiter", "executor")
DIRS = [(-1, 0), (0, 1), (1, 0), (0, -1)]
DIR_GLYPHS = ["^", ">", "v", "<"]

ECO_KINDS = {"root", "charge", "charge_dead", "flare"}
COMBATANT_KINDS = {"arbiter", "latcher", "executor", "executor_breached"}


class Game:
    def __init__(self) -> None:
        self.board: List[List[Cell]] = [[Cell() for _ in range(3)] for _ in range(3)]
        self.turn: int = 1
        self.last: str = ""
        self.spawn_index: int = 0
        self.spawn_bag: List[str] = []
        self.spawning_enabled: bool = True
        self.cleared_count: int = 0
        self.collapsed: bool = False
        self.collapse_reason: str = ""
        self.collapse_preview: str = ""
        self.clean_flags = {
            "charge_decayed": 0,
            "dead_charge_cleared": 0,
            "flare_expired": 0,
            "latcher_regressed": 0,
            "eco_destroyed": 0,
            "wasted_touch": 0,
            "cancelled_power": 0,
        }

    def reset_tracking(self) -> None:
        self.turn = 1
        self.last = ""
        self.spawn_index = 0
        self.spawn_bag = []
        self.cleared_count = 0
        self.collapsed = False
        self.collapse_reason = ""
        self.collapse_preview = ""
        self.clean_flags = {
            "charge_decayed": 0,
            "dead_charge_cleared": 0,
            "flare_expired": 0,
            "latcher_regressed": 0,
            "eco_destroyed": 0,
            "wasted_touch": 0,
            "cancelled_power": 0,
        }

    def seed_board(self, board_number: int) -> None:
        self.board = [[Cell() for _ in range(3)] for _ in range(3)]
        self.reset_tracking()

        if board_number == 1:
            self.last = "Board 1: blank timing board."
            return
        if board_number == 2:
            self.set_cell("5", Cell("root"))
            self.set_cell("8", Cell("arbiter", 0))
            self.last = "Board 2: Root + Arbiter."
            return
        if board_number == 3:
            self.set_cell("7", Cell("root"))
            self.set_cell("8", Cell("arbiter", 0))
            self.set_cell("4", Cell("executor", direction=1))
            self.set_cell("5", Cell("charge", 1))
            self.set_cell("6", Cell("root"))
            self.set_cell("2", Cell("latcher", 0))
            self.last = "Board 3: mixed pressure."
            return
        if board_number == 4:
            self.set_cell("7", Cell("root"))
            self.set_cell("8", Cell("arbiter", 0))
            self.set_cell("9", Cell("flare"))
            self.set_cell("4", Cell("executor_breached", direction=1))
            self.set_cell("5", Cell("charge", 1))
            self.set_cell("6", Cell("root"))
            self.set_cell("1", Cell("root"))
            self.set_cell("2", Cell("latcher", 1, 1))
            self.set_cell("3", Cell("root"))
            self.last = "Board 4: ugly recovery board."
            return
        if board_number == 5:
            self.set_cell("7", Cell("executor", direction=1))
            self.set_cell("8", Cell("arbiter", 0))
            self.set_cell("9", Cell("latcher", 0))
            self.set_cell("5", Cell("root"))
            self.set_cell("6", Cell("arbiter", 0))
            self.set_cell("2", Cell("executor", direction=0))
            self.last = "Board 5: stalled pressure board."
            return

    def get_cell(self, n: str) -> Cell:
        r, c = NUM_TO_POS[n]
        return self.board[r][c]

    def set_cell(self, n: str, cell: Cell) -> None:
        r, c = NUM_TO_POS[n]
        self.board[r][c] = cell

    def empty_tiles(self) -> List[str]:
        return [n for n in NUM_TO_POS.keys() if self.get_cell(n).kind == "empty"]

    def eco_tiles(self) -> List[str]:
        return [n for n in NUM_TO_POS.keys() if self.get_cell(n).kind in ECO_KINDS]

    def power_tiles(self) -> List[str]:
        return [
            n for n in NUM_TO_POS.keys()
            if self.get_cell(n).kind == "flare"
            or (self.get_cell(n).kind == "charge" and self.get_cell(n).value in (1, 2))
        ]

    def power_summary(self) -> str:
        parts: List[str] = []
        for n in NUM_TO_POS.keys():
            cell = self.get_cell(n)
            if cell.kind == "flare":
                parts.append(color(f"{n}:F now", ECO))
            elif cell.kind == "charge" and cell.value == 2:
                parts.append(color(f"{n}:C2 now", ECO))
            elif cell.kind == "charge" and cell.value == 1:
                parts.append(color(f"{n}:C1 now", ECO))
        if not parts:
            return color("none", DIM)
        return " | ".join(parts)

    def power_hint(self) -> str:
        powers = self.power_tiles()
        if not powers:
            return ""
        return " Use " + " or ".join(self.power_summary_plain(n) for n in powers) + "."

    def power_summary_plain(self, n: str) -> str:
        cell = self.get_cell(n)
        if cell.kind == "flare":
            return f"{n}:F now"
        if cell.kind == "charge" and cell.value == 2:
            return f"{n}:C2 now"
        if cell.kind == "charge" and cell.value == 1:
            return f"{n}:C1 now"
        return n

    def clear_console(self) -> None:
        os.system("cls" if os.name == "nt" else "clear")

    def is_threatened(self, n: str) -> bool:
        for other in NUM_TO_POS.keys():
            cell = self.get_cell(other)
            if cell.kind in ("latcher", "executor", "executor_breached") and cell.target == n:
                return True
        return False

    def token_for(self, n: str) -> str:
        cell = self.get_cell(n)
        threatened = self.is_threatened(n) and cell.kind in ECO_KINDS

        if cell.kind == "empty":
            return pad_token(" ")
        if cell.kind == "root":
            return color(pad_token("R!" if threatened else "R"), ECO)
        if cell.kind == "charge":
            label = f"C{cell.value}" + ("!" if threatened else "")
            return color(pad_token(label), ECO)
        if cell.kind == "charge_dead":
            return color(pad_token("C0!" if threatened else "C0"), ECO)
        if cell.kind == "flare":
            return color(pad_token("F!" if threatened else "F"), ECO)
        if cell.kind == "arbiter":
            if cell.value == 1:
                return color(pad_token("A*" + DIR_GLYPHS[cell.direction]), ARB)
            return color(pad_token("A"), ARB)
        if cell.kind == "latcher":
            ranged = "*" + DIR_GLYPHS[cell.direction] if cell.target else ""
            if cell.value == 0:
                return color(pad_token("L" + ranged), LAT)
            return color(pad_token(f"L{cell.value}" + ranged), LAT)
        if cell.kind == "executor":
            label = "E" + DIR_GLYPHS[cell.direction] + ("!" if cell.target else "")
            return color(pad_token(label), EXEC)
        if cell.kind == "executor_breached":
            label = "e" + DIR_GLYPHS[cell.direction] + ("!" if cell.target else "")
            return color(pad_token(label), EXEC)
        return pad_token("?")

    def render(self) -> None:
        self.clear_console()
        print(color("SSS Logic Sandbox v21", BOLD))
        print(
            f"Turn {self.turn}    "
            f"Cleared: {self.cleared_count}    "
            f"Spawn: {'on' if self.spawning_enabled else 'off'}    "
            f"Clean: {self.clean_summary()}"
        )
        if self.last:
            print(color("Last:", DIM), self.last)
        print(color("Power:", DIM), self.power_summary())
        if self.collapsed:
            print(color("State:", WARN), "COLLAPSE / study")
            if self.collapse_reason:
                print(color("Why:", WARN), self.collapse_reason)
            if self.collapse_preview:
                print(color("Next:", WARN), self.collapse_preview)
        print()

        for row in ROWS:
            print(" ".join(f"[{self.token_for(n)}]" for n in row))

        print()
        print(color("Eco:", ECO) + " R | C2->C1->C0 | F   " + color("! = threatened", WARN))
        print(
            color("Enemies:", DIM)
            + " "
            + color("Arbiter A*^/A", ARB)
            + " | "
            + color("Latcher L*^/L1/L2", LAT)
            + " | "
            + color("Executor E^/e^", EXEC)
        )
        if self.collapsed:
            print(color("Controls:", DIM), "r reset | n preview next shift | w board | h help | q quit")
        else:
            print(color("Controls:", DIM), "numpad tile | w board | s spawn on/off | h help | q quit")
        print()

    def clean_summary(self) -> str:
        parts = []
        if self.clean_flags["charge_decayed"]:
            parts.append(f"charge decay x{self.clean_flags['charge_decayed']}")
        if self.clean_flags["dead_charge_cleared"]:
            parts.append(f"C0 clear x{self.clean_flags['dead_charge_cleared']}")
        if self.clean_flags["flare_expired"]:
            parts.append(f"flare expired x{self.clean_flags['flare_expired']}")
        if self.clean_flags["latcher_regressed"]:
            parts.append(f"latcher regress x{self.clean_flags['latcher_regressed']}")
        if self.clean_flags["eco_destroyed"]:
            parts.append(f"eco lost x{self.clean_flags['eco_destroyed']}")
        if self.clean_flags["wasted_touch"]:
            parts.append(f"wasted touch x{self.clean_flags['wasted_touch']}")
        if self.clean_flags["cancelled_power"]:
            parts.append(f"cancelled power x{self.clean_flags['cancelled_power']}")
        if not parts:
            return color("clean", GOOD)
        return color(", ".join(parts), WARN)

    def help(self) -> None:
        self.clear_console()
        print(color("Rules", BOLD))
        print(color("Eco:", ECO) + " R | C2 strong -> C1 normal -> C0 dead | F now")
        print(color("Enemies:", DIM))
        print(color("A", ARB), "= Arbiter present/off. Next beat becomes A*.")
        print(color("A*", ARB), "= Arbiter active shot. Direction shows the eco line it will hit if missed.")
        print(color("L!", LAT), "= Latcher ranged cue. Direction shows the eco line it will hit if not killed.")
        print(color("E>", EXEC), "= Executor direction. E^! means it is loaded to crush marked eco next shift.")
        print(color("!", WARN), "on eco means that tile is threatened.")
        print(color("Power:", DIM), "shows current C2/C1/F tiles as now. Select that tile to use it.")
        print()
        print("Empty -> Root immediately.")
        print("Root -> 2 for Flare F, 6 for Charge C2.")
        print("C2 = STRONG power. C1/F = NORMAL power.")
        print("Touching C2/C1/F previews targets, then accepts a numpad target directly.")
        print("Any board tile can be targeted. Misaligned targets consume the power and spend the turn.")
        print("C0 is dead clutter. Touching it clears it.")
        print()
        print(color("Powered actions", BOLD))
        print("Latcher: NORMAL advances 2 stages. STRONG advances 3 stages.")
        print("L1 holds for one ignored shift before regressing to L.")
        print("Executor: NORMAL E -> e, e -> gone. STRONG E/e -> gone.")
        print("Arbiter: A* resolves only while active. A is bad timing.")
        print()
        print(color("Enemy pressure", BOLD))
        print("Arbiter: spawns as A, advances to A*, then missed A* fires in shown direction.")
        print("Executor: moves forward, turns right when blocked/at edge, loads on eco, crushes next shift.")
        print("Latcher: every 3 turns queues a ranged shot, then removes furthest eco in that line unless killed.")
        print()
        print(color("Collapse", BOLD))
        print("When the board is full and more enemy pressure is coming, control collapses.")
        print("Collapse is a study state, not an auto-reset. Preview the next shift or reset by choice.")
        print()
        input("Press Enter...")

    def choose_board(self) -> None:
        self.clear_console()
        print("Choose board:")
        print("1: blank timing board")
        print("2: Root + active Arbiter")
        print("3: mixed pressure")
        print("4: ugly recovery board")
        print("5: stalled pressure board")
        choice = input("> ").strip()
        if choice in ("1", "2", "3", "4", "5"):
            self.seed_board(int(choice))
        else:
            self.last = "Board unchanged."

    def valid_actions_for(self, n: str) -> List[Tuple[str, str]]:
        cell = self.get_cell(n)
        if cell.kind == "empty":
            return [("root", "Empty -> Root")]
        if cell.kind == "root":
            return [("flare", "Ledger -> Flare"), ("charge", "Vortex -> Charge C2")]
        if cell.kind == "charge":
            if cell.value == 2:
                return [("use_strong", "Use C2 as STRONG power")]
            if cell.value == 1:
                return [("use_normal", "Use C1 as NORMAL power")]
        if cell.kind == "charge_dead":
            return [("clear_dead_charge", "Clear C0")]
        if cell.kind == "flare":
            return [("use_flare", "Use Flare as NORMAL power")]
        if cell.kind in COMBATANT_KINDS:
            return [("needs_power", "Needs C2/C1/F power first")]
        return []

    def powered_target_actions(self, source_n: str, power: int) -> List[Tuple[str, str, str]]:
        options: List[Tuple[str, str, str]] = []
        for n in NUM_TO_POS.keys():
            if n == source_n:
                continue
            cell = self.get_cell(n)
            if cell.kind == "arbiter":
                if cell.value == 1:
                    options.append((n, "resolve_arbiter", f"{n}: resolve A*"))
                else:
                    options.append((n, "bad_arbiter", f"{n}: A inactive / bad timing"))
            elif cell.kind == "latcher":
                progress = 1 + power
                next_value = cell.value + progress
                if next_value >= 3:
                    options.append((n, "progress_latcher", f"{n}: Latcher +{progress} -> gone"))
                else:
                    options.append((n, "progress_latcher", f"{n}: Latcher +{progress} -> L{next_value}"))
            elif cell.kind == "executor":
                if power >= 2:
                    options.append((n, "resolve_executor_from_closed", f"{n}: STRONG E -> gone"))
                else:
                    options.append((n, "breach_executor", f"{n}: NORMAL E -> e"))
            elif cell.kind == "executor_breached":
                options.append((n, "resolve_executor", f"{n}: e -> gone"))
        return options

    def preview_label_for(self, action_code: str, power: int, cell: Cell) -> str:
        if action_code == "resolve_arbiter":
            return "A*->0"
        if action_code == "bad_arbiter":
            return "A bad"
        if action_code == "progress_latcher":
            progress = 1 + power
            next_value = cell.value + progress
            if next_value >= 3:
                return "L->0"
            return f"L->L{next_value}"
        if action_code == "breach_executor":
            return "E->e"
        if action_code == "resolve_executor_from_closed":
            return "E->0"
        if action_code == "resolve_executor":
            return "e->0"
        return "miss"

    def print_power_preview(self, source_n: str, power: int) -> None:
        options = self.powered_target_actions(source_n, power)
        option_by_tile = {target_n: action_code for target_n, action_code, _description in options}
        power_name = "STRONG" if power >= 2 else "NORMAL"
        print()
        print(f"{source_n}: {power_name} power preview")
        for row in ROWS:
            preview_cells = []
            for n in row:
                if n == source_n:
                    preview_cells.append(color(pad_token("source"), DIM))
                    continue
                action_code = option_by_tile.get(n, "")
                if action_code:
                    preview_cells.append(pad_token(self.preview_label_for(action_code, power, self.get_cell(n))))
                else:
                    preview_cells.append(color(pad_token("miss"), DIM))
            print(" ".join(f"[{label}]" for label in preview_cells))
        print()
        print("Select target tile by numpad position. 0 cancels.")

    def ask_power_target(self, source_n: str, power: int) -> Optional[Tuple[str, str]]:
        self.print_power_preview(source_n=source_n, power=power)
        option_by_tile = {
            target_n: action_code
            for target_n, action_code, _description in self.powered_target_actions(source_n, power)
        }
        choice = input("Target tile > ").strip().lower()
        if choice in ("", "0"):
            return None
        if choice not in NUM_TO_POS:
            return None
        action_code = option_by_tile.get(choice, "miss_power")
        return (choice, action_code)

    def select_action(self, n: str) -> Optional[Tuple[str, Optional[str], Optional[int]]]:
        actions = self.valid_actions_for(n)
        if not actions:
            self.clean_flags["wasted_touch"] += 1
            self.last = "No valid action."
            return None
        if len(actions) == 1:
            action = actions[0][0]
            if action == "use_strong":
                target = self.ask_power_target(source_n=n, power=2)
                if target is None:
                    self.clean_flags["cancelled_power"] += 1
                    return None
                target_n, target_action = target
                return (target_action, target_n, 2)
            if action in ("use_normal", "use_flare"):
                target = self.ask_power_target(source_n=n, power=1)
                if target is None:
                    self.clean_flags["cancelled_power"] += 1
                    return None
                target_n, target_action = target
                return (target_action, target_n, 1)
            return (action, n, None)

        print(f"Selected {n}: {self.token_for(n)}")
        print("2: Root -> Flare F")
        print("6: Root -> Charge C2")
        print("0: cancel")
        choice = input("> ").strip().lower()
        if choice in ("", "0"):
            return None
        if choice == "2":
            return ("flare", n, None)
        if choice == "6":
            return ("charge", n, None)
        self.clean_flags["wasted_touch"] += 1
        return None

    def apply_action(self, source_n: str, action: str, target_n: Optional[str], power: Optional[int]) -> None:
        message = ""
        if action == "root":
            self.set_cell(source_n, Cell("root"))
            message = f"{source_n}: Empty -> Root"
        elif action == "flare":
            self.set_cell(source_n, Cell("flare"))
            message = f"{source_n}: Root + Ledger -> Flare. Power now: {source_n}:F now"
        elif action == "charge":
            self.set_cell(source_n, Cell("charge", 2))
            message = f"{source_n}: Root + Vortex -> Charge C2. Power now: {source_n}:C2 now"
        elif action == "clear_dead_charge":
            self.clean_flags["dead_charge_cleared"] += 1
            self.set_cell(source_n, Cell())
            message = f"{source_n}: C0 cleared"
        elif action == "needs_power":
            self.clean_flags["wasted_touch"] += 1
            message = f"{source_n}: Needs C2/C1/F power first." + self.power_hint()
        else:
            message = self.apply_powered_action(target_n=source_n, action=action, power=power or 0)

        world_messages = self.resolve_world(source_n=source_n, action=action, target_n=target_n)
        if world_messages:
            message += " | " + " | ".join(world_messages)
        self.last = message
        self.turn += 1

    def apply_powered_action(self, target_n: str, action: str, power: int) -> str:
        cell = self.get_cell(target_n)
        if action == "resolve_arbiter":
            if cell.kind == "arbiter" and cell.value == 1:
                self.cleared_count += 1
                self.set_cell(target_n, Cell())
                return f"{target_n}: A* resolved"
            self.clean_flags["wasted_touch"] += 1
            return f"{target_n}: Arbiter timing failed"
        if action == "bad_arbiter":
            self.clean_flags["wasted_touch"] += 1
            return f"{target_n}: A inactive; timing was poor"
        if action == "progress_latcher":
            if cell.kind != "latcher":
                self.clean_flags["wasted_touch"] += 1
                return f"{target_n}: no Latcher result"
            progress = 1 + power
            next_value = cell.value + progress
            if next_value >= 3:
                self.cleared_count += 1
                self.set_cell(target_n, Cell())
                return f"{target_n}: Latcher completed"
            hold = 1 if next_value == 1 else 0
            self.set_cell(target_n, Cell("latcher", next_value, hold, target=cell.target, timer=cell.timer))
            return f"{target_n}: Latcher progressed to L{next_value}"
        if action == "breach_executor":
            self.set_cell(target_n, Cell("executor_breached", direction=cell.direction, target=cell.target))
            return f"{target_n}: Executor breached E -> e"
        if action == "resolve_executor_from_closed":
            self.cleared_count += 1
            self.set_cell(target_n, Cell())
            return f"{target_n}: STRONG Executor resolve E -> gone"
        if action == "resolve_executor":
            self.cleared_count += 1
            self.set_cell(target_n, Cell())
            return f"{target_n}: Executor resolved"
        if action == "miss_power":
            self.clean_flags["wasted_touch"] += 1
            return f"{target_n}: power found no result"
        self.clean_flags["wasted_touch"] += 1
        return "Unknown powered action"

    def resolve_world(self, source_n: str, action: str, target_n: Optional[str]) -> List[str]:
        messages: List[str] = []
        messages.extend(self.resolve_eco_decay(source_n=source_n, action=action))
        messages.extend(self.resolve_latcher_regression(action=action, target_n=target_n))
        messages.extend(self.resolve_arbiter_pressure())
        messages.extend(self.resolve_latcher_pressure())
        messages.extend(self.resolve_executor_pressure())
        spawn_message = self.maybe_spawn_combatant()
        if spawn_message:
            messages.append(spawn_message)
        collapse_message = self.collapse_if_needed()
        if collapse_message:
            messages.append(collapse_message)
        return messages

    def resolve_eco_decay(self, source_n: str, action: str) -> List[str]:
        messages: List[str] = []
        for n in list(NUM_TO_POS.keys()):
            if n == source_n and action in ("charge", "clear_dead_charge"):
                continue
            cell = self.get_cell(n)
            if cell.kind == "charge":
                if cell.value == 2:
                    self.set_cell(n, Cell("charge", 1))
                    self.clean_flags["charge_decayed"] += 1
                    messages.append(f"{n}: C2 decayed to C1")
                elif cell.value == 1:
                    self.set_cell(n, Cell("charge_dead", 0))
                    self.clean_flags["charge_decayed"] += 1
                    messages.append(f"{n}: C1 decayed to C0")
        for n in list(NUM_TO_POS.keys()):
            if n == source_n and action == "flare":
                continue
            cell = self.get_cell(n)
            if cell.kind == "flare":
                self.set_cell(n, Cell())
                self.clean_flags["flare_expired"] += 1
                messages.append(f"{n}: Flare expired")
        return messages

    def resolve_latcher_regression(self, action: str, target_n: Optional[str]) -> List[str]:
        messages: List[str] = []
        for n in list(NUM_TO_POS.keys()):
            if action == "progress_latcher" and n == target_n:
                continue
            cell = self.get_cell(n)
            if cell.kind == "latcher" and cell.value > 0:
                if cell.value == 1 and cell.hold > 0:
                    self.set_cell(n, Cell("latcher", 1, 0, target=cell.target, timer=cell.timer))
                    messages.append(f"{n}: L1 held")
                elif cell.value == 1:
                    self.set_cell(n, Cell("latcher", 0, target=cell.target, timer=cell.timer))
                    self.clean_flags["latcher_regressed"] += 1
                    messages.append(f"{n}: L1 regressed to L")
                elif cell.value == 2:
                    self.set_cell(n, Cell("latcher", 1, 1, target=cell.target, timer=cell.timer))
                    self.clean_flags["latcher_regressed"] += 1
                    messages.append(f"{n}: L2 regressed to L1")
        return messages

    def adjacent_tiles(self, n: str) -> List[str]:
        r, c = NUM_TO_POS[n]
        out: List[str] = []
        for dr, dc in DIRS:
            candidate = POS_TO_NUM.get((r + dr, c + dc), "")
            if candidate:
                out.append(candidate)
        return out

    def ray_tiles(self, n: str, direction: int) -> List[str]:
        r, c = NUM_TO_POS[n]
        dr, dc = DIRS[direction]
        out: List[str] = []
        while True:
            r += dr
            c += dc
            candidate = POS_TO_NUM.get((r, c), "")
            if not candidate:
                break
            out.append(candidate)
        return out

    def eco_in_direction(self, n: str, direction: int) -> List[str]:
        return [candidate for candidate in self.ray_tiles(n, direction) if self.get_cell(candidate).kind in ECO_KINDS]

    def closest_eco_in_direction(self, n: str, direction: int) -> Optional[str]:
        eco = self.eco_in_direction(n, direction)
        return eco[0] if eco else None

    def furthest_eco_in_direction(self, n: str, direction: int) -> Optional[str]:
        eco = self.eco_in_direction(n, direction)
        return eco[-1] if eco else None

    def choose_direction_with_eco(self, n: str, prefer_furthest: bool) -> int:
        options: List[Tuple[int, int]] = []
        for direction in range(4):
            eco = self.eco_in_direction(n, direction)
            if eco:
                distance = len(self.ray_tiles(n, direction)) if prefer_furthest else self.ray_tiles(n, direction).index(eco[0]) + 1
                options.append((direction, distance))
        if not options:
            return self.get_cell(n).direction
        if prefer_furthest:
            best_distance = max(distance for _direction, distance in options)
            best = [direction for direction, distance in options if distance == best_distance]
        else:
            best_distance = min(distance for _direction, distance in options)
            best = [direction for direction, distance in options if distance == best_distance]
        return random.choice(best)

    def destroy_eco(self, n: str, reason: str) -> Optional[str]:
        if self.get_cell(n).kind not in ECO_KINDS:
            return None
        self.set_cell(n, Cell())
        self.clean_flags["eco_destroyed"] += 1
        return f"{reason}: {n} eco destroyed"

    def resolve_arbiter_pressure(self) -> List[str]:
        messages: List[str] = []
        for n in list(NUM_TO_POS.keys()):
            cell = self.get_cell(n)
            if cell.kind != "arbiter":
                continue
            if cell.value == 1:
                target = self.closest_eco_in_direction(n, cell.direction)
                if target:
                    msg = self.destroy_eco(target, f"{n}: A*{DIR_GLYPHS[cell.direction]} missed")
                    if msg:
                        messages.append(msg)
                self.set_cell(n, Cell("arbiter", 0, direction=cell.direction))
                messages.append(f"{n}: A*{DIR_GLYPHS[cell.direction]} -> A")
            else:
                direction = self.choose_direction_with_eco(n, prefer_furthest=False)
                self.set_cell(n, Cell("arbiter", 1, direction=direction))
                messages.append(f"{n}: A -> A*{DIR_GLYPHS[direction]}")
        return messages

    def resolve_latcher_pressure(self) -> List[str]:
        messages: List[str] = []
        for n in list(NUM_TO_POS.keys()):
            cell = self.get_cell(n)
            if cell.kind != "latcher":
                continue
            if cell.target and cell.timer <= 0:
                target = cell.target
                if self.get_cell(target).kind in ECO_KINDS:
                    msg = self.destroy_eco(target, f"{n}: L*{DIR_GLYPHS[cell.direction]} attack")
                    if msg:
                        messages.append(msg)
                else:
                    messages.append(f"{n}: L*{DIR_GLYPHS[cell.direction]} found no eco at {target}")
                current = self.get_cell(n)
                if current.kind == "latcher":
                    current.target = ""
                    current.timer = 0
                continue
            if cell.target and cell.timer > 0:
                cell.timer -= 1
                messages.append(f"{n}: L*{DIR_GLYPHS[cell.direction]} targeting {cell.target}")
                continue
            if self.turn % 3 == 0:
                target_info = self.choose_latcher_target(n)
                if target_info:
                    target, direction = target_info
                    cell.target = target
                    cell.direction = direction
                    cell.timer = 1
                    messages.append(f"{n}: L*{DIR_GLYPHS[direction]} marked {target}")
        return messages

    def choose_latcher_target(self, latcher_n: str) -> Optional[Tuple[str, int]]:
        direction = self.choose_direction_with_eco(latcher_n, prefer_furthest=True)
        target = self.furthest_eco_in_direction(latcher_n, direction)
        if not target:
            return None
        return target, direction

    def forward_tile(self, n: str, direction: int) -> Optional[str]:
        r, c = NUM_TO_POS[n]
        dr, dc = DIRS[direction]
        return POS_TO_NUM.get((r + dr, c + dc))

    def resolve_executor_pressure(self) -> List[str]:
        messages: List[str] = []
        executor_tiles = [n for n in NUM_TO_POS.keys() if self.get_cell(n).kind in ("executor", "executor_breached")]
        for n in executor_tiles:
            cell = self.get_cell(n)
            if cell.kind not in ("executor", "executor_breached"):
                continue
            if cell.target:
                target = cell.target
                if self.get_cell(target).kind in ECO_KINDS:
                    msg = self.destroy_eco(target, f"{n}: Executor crush")
                    if msg:
                        messages.append(msg)
                else:
                    messages.append(f"{n}: Executor crush found no eco at {target}")
                current = self.get_cell(n)
                if current.kind in ("executor", "executor_breached"):
                    current.target = ""
                continue
            front = self.forward_tile(n, cell.direction)
            if front is None:
                cell.direction = (cell.direction + 1) % 4
                messages.append(f"{n}: Executor turned right")
                continue
            front_cell = self.get_cell(front)
            if front_cell.kind in ECO_KINDS:
                cell.target = front
                messages.append(f"{n}: Executor loaded on {front}")
                continue
            if front_cell.kind == "empty":
                moved = Cell(cell.kind, cell.value, cell.hold, cell.direction, cell.target, cell.timer)
                self.set_cell(front, moved)
                self.set_cell(n, Cell())
                messages.append(f"{n}->{front}: Executor moved")
                continue
            cell.direction = (cell.direction + 1) % 4
            messages.append(f"{n}: Executor blocked, turned right")
        return messages

    def spawn_count_for_turn(self, turn_number: Optional[int] = None) -> int:
        # Pressure curve v22:
        # - First contact arrives after the first eco action.
        # - Then the board gets breathing room to mature before sustained enemy pressure.
        # - Pressure ramps from every 3rd turn, to every other turn, to every turn.
        # Enemy behavior is unchanged; this only controls composition/rate.
        t = self.turn if turn_number is None else turn_number
        if t == 1:
            return 1
        if t < 8:
            return 0
        if t < 16:
            return 1 if t % 3 == 1 else 0
        if t < 28:
            return 1 if t % 2 == 1 else 0
        return 1

    def board_is_full(self) -> bool:
        return not self.empty_tiles()

    def collapse_if_needed(self) -> Optional[str]:
        if self.collapsed:
            return None
        if not self.board_is_full():
            return None

        next_turn = self.turn + 1
        if not self.eco_tiles():
            return self.enter_collapse("Board is full and the eco engine is gone.")
        if self.spawning_enabled and self.spawn_count_for_turn(next_turn) > 0:
            return self.enter_collapse(f"Board is full; enemy pressure is scheduled for turn {next_turn}.")
        return None

    def enter_collapse(self, reason: str) -> str:
        self.collapsed = True
        self.collapse_reason = reason
        self.collapse_preview = self.preview_next_world_shift()
        return "Collapse: " + reason

    def preview_next_world_shift(self) -> str:
        preview_game = copy.deepcopy(self)
        preview_game.collapsed = False
        preview_game.collapse_reason = ""
        preview_game.collapse_preview = ""
        messages = preview_game.resolve_world(source_n="", action="collapse_preview", target_n=None)
        if not messages:
            return "No further shift."
        return " | ".join(messages[:5]) + (" | ..." if len(messages) > 5 else "")

    def refresh_collapse_preview(self) -> None:
        if not self.collapsed:
            return
        self.collapse_preview = self.preview_next_world_shift()
        self.last = "The eye follows. The board no longer obeys."

    def next_spawn_kind(self) -> str:
        if not self.spawn_bag:
            self.spawn_bag = list(SPAWN_KIND_ORDER)
            random.shuffle(self.spawn_bag)
        return self.spawn_bag.pop()

    def maybe_spawn_combatant(self) -> Optional[str]:
        if not self.spawning_enabled:
            return None
        spawn_count = self.spawn_count_for_turn()
        if spawn_count <= 0:
            return None
        messages: List[str] = []
        for _i in range(spawn_count):
            empty = self.empty_tiles()
            if not empty:
                break
            spawn_kind = self.next_spawn_kind()
            spawn_tile = random.choice(empty)
            self.spawn_index += 1
            if spawn_kind == "latcher":
                self.set_cell(spawn_tile, Cell("latcher", 0))
                messages.append(f"{spawn_tile}: Latcher appeared")
            elif spawn_kind == "arbiter":
                self.set_cell(spawn_tile, Cell("arbiter", 0))
                messages.append(f"{spawn_tile}: Arbiter appeared")
            elif spawn_kind == "executor":
                self.set_cell(spawn_tile, Cell("executor", direction=random.randrange(4)))
                messages.append(f"{spawn_tile}: Executor appeared {DIR_GLYPHS[self.get_cell(spawn_tile).direction]}")
        if not messages:
            return None
        return " | ".join(messages)

    def consume_power_tile_and_apply(self, source_n: str, action: str, target_n: str, power: int) -> None:
        source_cell = self.get_cell(source_n)
        self.set_cell(source_n, Cell())
        message = self.apply_powered_action(target_n=target_n, action=action, power=power)
        world_messages = self.resolve_world(source_n=source_n, action=action, target_n=target_n)
        if source_cell.kind == "charge" and source_cell.value == 2:
            source_label = "C2"
        elif source_cell.kind == "charge" and source_cell.value == 1:
            source_label = "C1"
        else:
            source_label = "F"
        message = f"{source_n}: {source_label} used -> {message}"
        if world_messages:
            message += " | " + " | ".join(world_messages)
        self.last = message
        self.turn += 1

    def run(self) -> None:
        self.seed_board(1)
        while True:
            self.render()
            choice = input("Select numpad tile > ").strip().lower()
            if choice == "q":
                break
            if choice == "h":
                self.help()
                continue
            if choice == "w":
                self.choose_board()
                continue
            if choice == "s" and not self.collapsed:
                self.spawning_enabled = not self.spawning_enabled
                self.last = "Spawning toggled " + ("on." if self.spawning_enabled else "off.")
                continue
            if self.collapsed:
                if choice == "r":
                    self.seed_board(1)
                    self.last = "Reset from collapse."
                    continue
                if choice == "n":
                    self.refresh_collapse_preview()
                    continue
                if choice == "w":
                    self.choose_board()
                    continue
                self.last = "The board is past control. Study it, preview with n, or reset with r."
                continue
            if choice not in NUM_TO_POS:
                self.clean_flags["wasted_touch"] += 1
                self.last = "Invalid tile input."
                continue
            selected = self.select_action(choice)
            if selected is None:
                self.last = "Cancelled."
                continue
            action, target_n, power = selected
            if power is not None and target_n is not None:
                self.consume_power_tile_and_apply(source_n=choice, action=action, target_n=target_n, power=power)
            else:
                self.apply_action(source_n=choice, action=action, target_n=choice, power=None)


if __name__ == "__main__":
    Game().run()
