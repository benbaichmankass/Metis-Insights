"""M29 P1 — the causal-loop structure descriptor.

A stock-flow `Model` (engine.py) carries executable rate functions; this module
carries the *legible* companion: a serializable description of the causal graph —
nodes, signed links (polarity), and the feedback loops they form — so a model's
structure can be committed, versioned, diffed, and rendered without executing it.

This is what keeps M29 legible rather than a black box (the design's non-negotiable):
the AI fits *parameters within a declared structure*; the structure itself is this
human-readable, reviewable artifact. `to_dict()` round-trips to JSON for a
point-in-time-versioned committed spec.

Pure stdlib, no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Polarity = Literal["+", "-"]


@dataclass(frozen=True)
class Link:
    """A signed causal edge ``src -> dst``. ``+`` = same direction (more src →
    more dst); ``-`` = opposite (more src → less dst). ``delayed`` marks an edge
    that carries a material lag (the loops that oscillate)."""

    src: str
    dst: str
    polarity: Polarity
    delayed: bool = False
    note: str = ""

    def to_dict(self) -> dict:
        return {
            "src": self.src,
            "dst": self.dst,
            "polarity": self.polarity,
            "delayed": self.delayed,
            "note": self.note,
        }


@dataclass(frozen=True)
class Loop:
    """A feedback loop: an ordered node cycle + its kind. ``reinforcing`` (R) loops
    amplify; ``balancing`` (B) loops counteract (net negative polarity around the
    cycle). Recorded by hand for legibility — the loop *kind* is the whole point of
    an SD model."""

    name: str
    kind: Literal["reinforcing", "balancing"]
    nodes: tuple[str, ...]
    note: str = ""

    def to_dict(self) -> dict:
        return {"name": self.name, "kind": self.kind, "nodes": list(self.nodes), "note": self.note}


@dataclass(frozen=True)
class CausalStructure:
    """The committed, versioned description of a model's causal graph.

    ``version`` is a monotonic string (bump on any structural change — a new
    node/link/loop or a polarity flip — so a stored spec is unambiguous). ``stocks``
    and ``auxiliaries`` partition the nodes (accumulations vs read-off quantities).
    """

    model: str
    version: str
    stocks: tuple[str, ...]
    flows: tuple[str, ...] = ()
    auxiliaries: tuple[str, ...] = ()
    exogenous: tuple[str, ...] = ()
    links: tuple[Link, ...] = ()
    loops: tuple[Loop, ...] = ()
    description: str = ""

    def nodes(self) -> tuple[str, ...]:
        # In a causal-loop diagram the flows (rate variables) are nodes too.
        return (
            tuple(self.stocks)
            + tuple(self.flows)
            + tuple(self.auxiliaries)
            + tuple(self.exogenous)
        )

    def validate(self) -> None:
        """Every link endpoint must be a declared node; loop nodes too."""
        known = set(self.nodes())
        for lk in self.links:
            for role, n in (("src", lk.src), ("dst", lk.dst)):
                if n not in known:
                    raise ValueError(
                        f"link {lk.src}->{lk.dst}: {role}={n!r} is not a declared node {sorted(known)}"
                    )
        for lp in self.loops:
            for n in lp.nodes:
                if n not in known:
                    raise ValueError(f"loop {lp.name!r}: node {n!r} is not a declared node")

    def to_dict(self) -> dict:
        return {
            "model": self.model,
            "version": self.version,
            "stocks": list(self.stocks),
            "flows": list(self.flows),
            "auxiliaries": list(self.auxiliaries),
            "exogenous": list(self.exogenous),
            "links": [lk.to_dict() for lk in self.links],
            "loops": [lp.to_dict() for lp in self.loops],
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "CausalStructure":
        return cls(
            model=d["model"],
            version=d["version"],
            stocks=tuple(d.get("stocks", [])),
            flows=tuple(d.get("flows", [])),
            auxiliaries=tuple(d.get("auxiliaries", [])),
            exogenous=tuple(d.get("exogenous", [])),
            links=tuple(
                Link(
                    src=x["src"],
                    dst=x["dst"],
                    polarity=x["polarity"],
                    delayed=x.get("delayed", False),
                    note=x.get("note", ""),
                )
                for x in d.get("links", [])
            ),
            loops=tuple(
                Loop(name=x["name"], kind=x["kind"], nodes=tuple(x["nodes"]), note=x.get("note", ""))
                for x in d.get("loops", [])
            ),
            description=d.get("description", ""),
        )
