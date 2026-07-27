"""Case library -- originally written for the superseded pot/split-steal
game (see design/DESIGN.md: "Life Rolls, not Split Decision"). Still
fetched by server.py's run_one_tournament for a scenario/pot flavor label,
but NOT currently rendered anywhere in web/index.html's tournament UI --
a real, known gap (dead weight right now), not a design decision. Wiring
a case's scenario into the tournament's table/billboard, or writing a
fresh library actually themed around Liar's Dice stakes, is real work
still owed here.

Same relationship to a running show that Dominion's domain library has to a
duel: a growing, shared pool of authored scenarios, not a fixed set.
Growing this library is just adding another Case entry to CASE_LIBRARY, in
this same shape.

Every case pairs a game-theory framework with a "death on the line" stake
drawn from one of four registers (see design/DESIGN.md): literal,
social/psychological, existential/livelihood, or absurd-but-earnest. Case
subject matter stays personal, workplace, family, or community-scale, or
clearly fictional -- never a real-world political conflict.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import List


@dataclass(frozen=True)
class Case:
    name: str
    framework: str            # "Ultimatum" | "Trust" | "Stag Hunt" | "Chicken"
    stakes_register: str      # "literal" | "social" | "existential" | "absurd"
    scenario: str             # read aloud to teams and audience at case-reveal
    pot_label: str            # what's actually being split, in-fiction
    pot_value: int            # normalized points; the actual split/steal math runs on this


CASE_LIBRARY: List[Case] = [
    Case(
        name="Two Doors Down",
        framework="Ultimatum",
        stakes_register="existential",
        scenario=(
            "Two family-run hardware stores, two blocks apart, have run this "
            "town for thirty years each. A big-box store is opening next "
            "spring, and everyone on Main Street knows only one hardware "
            "store survives that -- together, or not at all. Tonight, one "
            "family proposes how ownership of the merged store gets split. "
            "The other can accept it, or refuse -- and if they refuse, "
            "there's no merger. Both stores close within the year."
        ),
        pot_label="ownership of the merged store",
        pot_value=100,
    ),
    Case(
        name="The Last Recipe",
        framework="Trust",
        stakes_register="existential",
        scenario=(
            "Two food trucks are competing for the single vendor slot at "
            "this city's biggest festival -- a season's worth of business "
            "riding on one weekend. A joint truck, combining both signature "
            "dishes, would be the strongest application by far. But someone "
            "has to show their actual recipe first, on camera, to prove "
            "they're serious -- and whoever moves first hands the other "
            "everything they'd need to walk away and use it alone."
        ),
        pot_label="the festival vendor slot",
        pot_value=100,
    ),
    Case(
        name="The Irrigation Line",
        framework="Stag Hunt",
        stakes_register="existential",
        scenario=(
            "Two neighboring family farms share one aging irrigation line. "
            "Fixed properly, with both families splitting the real cost, "
            "both harvests are saved outright. Fixed by only one family "
            "alone, it barely holds -- they eat the whole cost for a "
            "fraction of the benefit, while the other free-rides on what "
            "little gets through. Left unfixed, both crops fail before "
            "August."
        ),
        pot_label="this season's harvest",
        pot_value=100,
    ),
    Case(
        name="The Last Case",
        framework="Chicken",
        stakes_register="absurd",
        scenario=(
            "Two of the town's most competitive backyard pitmasters have "
            "each, independently, bought out what they believed was the "
            "region's entire remaining stock of a single rare hot sauce -- "
            "the one ingredient either of their signature entries actually "
            "needs to win tomorrow's cook-off. There is exactly one case "
            "left, and both of them are holding a receipt for it. Neither "
            "is backing down. If neither yields, the standoff itself "
            "becomes the story at tomorrow's cook-off -- and it will not "
            "make either of them look good."
        ),
        pot_label="the last case of hot sauce",
        pot_value=100,
    ),
]
