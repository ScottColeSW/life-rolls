"""Persona archetypes -- still fully current (this file's ROSTER is what
draw_cast pulls the tournament's seated players from, see engine/liars_dice.py
and design/DESIGN.md's "Life Rolls, not Split Decision"). Written for the
original pot/split-steal game this project has since moved past, but the
archetype pool, temperament rolls, and private-incentive flavor all still
apply directly to a Liar's Dice seat.

Mandate archetypes are the authored content -- the reusable building blocks
personas get drawn from at team-assignment time, the same relationship
Dominion's profession list has to that show's individual cast: a growing,
shared pool, not a fixed static roster. Growing the cast is just adding
another MandateArchetype entry to ROSTER below, in this same shape --
nothing else needs to change. Adding more texture to an existing archetype
is just adding another string to its incentive_templates list.

Temperament (risk_tolerance, trust_propensity) and the private incentive are
NOT fixed on the archetype -- they're rolled/picked fresh every time an
archetype is drawn for an episode (see draw_persona), so the same "Diner
Owner" archetype produces a genuinely different character run to run:
different exact risk/trust numbers within that archetype's plausible range,
a different specific secret pulled from its own template pool. This is what
lets a small, hand-authored archetype list produce a large, varied cast
without needing one fully-unique entry per character that will ever appear
on the show.

Cast principle: these are meant to read as regular people from all walks of
American life -- corporate, institutional, and working-class alike, a range
of ages and backgrounds -- not a cast of executives and diplomats. Case
subject matter itself stays away from real-world political conflicts (see
design/DESIGN.md); these archetypes and their stakes should stay personal,
workplace, family, or community-scale, or clearly fictional/absurd.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Tuple
import random


@dataclass(frozen=True)
class MandateArchetype:
    """An authored building block, not a character -- see draw_persona()."""
    name: str
    mandate: str
    # 0.0 = won't gamble at all .. 1.0 = bets everything without blinking.
    risk_tolerance_range: Tuple[float, float]
    # 0.0 = assumes betrayal by default .. 1.0 = assumes good faith by default.
    trust_propensity_range: Tuple[float, float]
    incentive_templates: List[str]
    # Qualitative behavioral texture that a single number can't capture
    # (e.g. "starts trusting but flips hard after one betrayal") -- flavor
    # for prompt-writing later, not consulted by draw_persona itself.
    notes: str = ""


@dataclass(frozen=True)
class Persona:
    """One concrete, rolled instance of an archetype -- what actually shows
    up on a panel. Two Personas drawn from the same archetype in different
    episodes will differ in exact temperament and exact private incentive,
    while still reading as recognizably the same kind of person."""
    archetype_name: str
    mandate: str
    risk_tolerance: float
    trust_propensity: float
    private_incentive: str
    notes: str = ""


ROSTER: List[MandateArchetype] = [
    MandateArchetype(
        name="The CFO",
        mandate="Represents shareholders; needs a defensible quarterly number.",
        risk_tolerance_range=(0.05, 0.25),
        trust_propensity_range=(0.10, 0.30),
        incentive_templates=[
            "Personally exposed -- a bad outcome here could cost THEM their "
            "job, not just the company's, a stake they'd never say out loud.",
        ],
        notes="Plays it safe in public even when a bolder move would pay off better.",
    ),
    MandateArchetype(
        name="The Executor",
        mandate="Eldest sibling, administering a disputed family estate on everyone's behalf.",
        risk_tolerance_range=(0.35, 0.55),
        trust_propensity_range=(0.55, 0.80),
        incentive_templates=[
            "Quietly in personal debt -- needs their cut more than they're "
            "letting the family believe.",
        ],
        notes="Starts high-trust, family-first -- but a single betrayal flips this hard for the rest of the show.",
    ),
    MandateArchetype(
        name="The Union Rep",
        mandate="Speaks for a membership, not for themselves.",
        risk_tolerance_range=(0.15, 0.35),
        trust_propensity_range=(0.40, 0.60),
        incentive_templates=[
            "May already have a tentative side-deal with the other side's "
            "members that their own team doesn't know about yet.",
        ],
        notes="Stubborn on principle -- will hold a position sheer willpower alone, even when the math says fold.",
    ),
    MandateArchetype(
        name="The Founder",
        mandate="Represents a startup that doesn't survive a bad outcome here.",
        risk_tolerance_range=(0.70, 0.95),
        trust_propensity_range=(0.40, 0.65),
        incentive_templates=[
            "Already knows the company is in worse shape than they're "
            "presenting -- win or lose here, that doesn't change.",
        ],
        notes="Bet-the-company energy -- talks like the stakes are even higher than they actually are.",
    ),
    MandateArchetype(
        name="The Diplomat",
        mandate="Represents an institution, not themselves -- procedural, rule-bound.",
        risk_tolerance_range=(0.20, 0.40),
        trust_propensity_range=(0.35, 0.55),
        incentive_templates=[
            "Carries orders from above that quietly contradict the public "
            "position they're required to hold.",
        ],
        notes="Trusts process more than people; falls back on protocol language under pressure.",
    ),
    MandateArchetype(
        name="The Adjuster",
        mandate="Pure numbers, no emotional stake -- evaluates this the way an actuary evaluates risk.",
        risk_tolerance_range=(0.40, 0.60),
        trust_propensity_range=(0.40, 0.60),
        incentive_templates=[
            "Nothing hidden -- genuinely exactly what they claim to be, "
            "which is its own kind of unsettling to play against.",
        ],
        notes="The clean foil -- plays the literal game-theoretic optimum, no theatrics.",
    ),
    MandateArchetype(
        name="The Diner Owner",
        mandate="Runs the neighborhood's whole gathering place -- second-generation, name's on the door.",
        risk_tolerance_range=(0.30, 0.50),
        trust_propensity_range=(0.60, 0.85),
        incentive_templates=[
            "The diner is two months from closing and they haven't told "
            "their own kids yet -- this might be the only way out.",
        ],
        notes="Warm and high-trust by default -- but it's personal, not strategic, once someone crosses them.",
    ),
    MandateArchetype(
        name="The Night-Shift Nurse",
        mandate="Has seen real life-and-death stakes for a living; low patience for anyone performing them.",
        risk_tolerance_range=(0.35, 0.55),
        trust_propensity_range=(0.45, 0.65),
        incentive_templates=[
            "Worn thin from overtime -- the money matters more to them "
            "right now than the principle does, and they know it.",
        ],
        notes="Blunt, low-drama -- the character most likely to puncture the room's melodrama on air.",
    ),
    MandateArchetype(
        name="The Long-Haul Trucker",
        mandate="Independent, skeptical of institutions and anyone who talks too smooth.",
        risk_tolerance_range=(0.30, 0.50),
        trust_propensity_range=(0.15, 0.35),
        incentive_templates=[
            "Lonelier than they let on -- an unexpected soft spot for "
            "whoever treats them like a person instead of a mark.",
        ],
        notes="Rock-solid once they actually commit to someone -- the hard part is getting there.",
    ),
    MandateArchetype(
        name="The Immigrant Shop Owner",
        mandate="Built something from nothing; the shop is the whole bet.",
        risk_tolerance_range=(0.65, 0.90),
        trust_propensity_range=(0.45, 0.70),
        incentive_templates=[
            "Takes 'the right to freedom' almost literally, personally -- "
            "will not be talked down to about what they've earned.",
        ],
        notes="Already risked everything once to get here -- a bad outcome today doesn't scare them the way it should.",
    ),
    MandateArchetype(
        name="The Gig Worker",
        mandate="Young, always online, already used to being watched -- treats most things as a hustle.",
        risk_tolerance_range=(0.60, 0.85),
        trust_propensity_range=(0.15, 0.35),
        incentive_templates=[
            "Actual public embarrassment barely registers -- they livestream "
            "their life anyway, which makes them oddly hard to threaten.",
        ],
        notes="Social-death stakes land differently on this one -- worth writing cases that notice that.",
    ),
]


def draw_persona(archetype: MandateArchetype, rng: random.Random) -> Persona:
    """Rolls one concrete Persona from an archetype -- fresh temperament
    within the archetype's range, one private incentive chosen from its
    template pool. Call this again for the same archetype in a later
    episode and you'll get a different, but recognizably-the-same-kind-of-
    person, result."""
    return Persona(
        archetype_name=archetype.name,
        mandate=archetype.mandate,
        risk_tolerance=round(rng.uniform(*archetype.risk_tolerance_range), 2),
        trust_propensity=round(rng.uniform(*archetype.trust_propensity_range), 2),
        private_incentive=rng.choice(archetype.incentive_templates),
        notes=archetype.notes,
    )


def draw_cast(count: int, rng: random.Random) -> List[Persona]:
    """Draws COUNT distinct archetypes (no repeats within one cast) and
    rolls a fresh Persona from each -- the actual team-assignment entry
    point episodes call. Raises if asked for more distinct archetypes than
    ROSTER currently has, rather than silently repeating one -- a repeat
    within a single cast (as opposed to across episodes, which is fine and
    expected) would read as a mistake, not a feature."""
    if count > len(ROSTER):
        raise ValueError(
            f"Only {len(ROSTER)} archetypes exist in ROSTER, cannot draw "
            f"{count} distinct ones for a single cast."
        )
    chosen = rng.sample(ROSTER, k=count)
    return [draw_persona(a, rng) for a in chosen]


if __name__ == "__main__":
    demo_rng = random.Random()
    print(f"ROSTER: {len(ROSTER)} archetypes\n")
    for p in draw_cast(4, demo_rng):
        print(f"{p.archetype_name}  (risk={p.risk_tolerance}, trust={p.trust_propensity})")
        print(f"  Mandate: {p.mandate}")
        print(f"  Secret:  {p.private_incentive}")
        if p.notes:
            print(f"  Notes:   {p.notes}")
        print()
