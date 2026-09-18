"""Provenance tagging.

Every academic claim that reaches a user carries one of these tags, and the UI
renders a visible badge for it. This is how the system satisfies the project
requirement to "distinguish verified information from AI-generated suggestions."

The rule enforced throughout the codebase:

    The audit engine will only ever consume facts tagged VERIFIED or
    STUDENT_CONFIRMED. An UNVERIFIED_EXTRACTION can never affect a degree audit.
"""

from enum import StrEnum

from pydantic import BaseModel, Field


class Provenance(StrEnum):
    #: Read from a catalog YAML file or computed by the deterministic audit engine.
    VERIFIED = "verified"

    #: Extracted by AI from an uploaded document, then explicitly confirmed by the student.
    STUDENT_CONFIRMED = "student_confirmed"

    #: Produced by the LLM: advice, phrasing, ranking commentary. Never a hard fact.
    AI_SUGGESTED = "ai_suggested"

    #: Extracted by AI and NOT yet confirmed. Must not reach the audit engine.
    UNVERIFIED_EXTRACTION = "unverified_extraction"


#: Tags the audit engine is permitted to build on.
TRUSTED_FOR_AUDIT: frozenset[Provenance] = frozenset(
    {Provenance.VERIFIED, Provenance.STUDENT_CONFIRMED}
)

#: Human-readable text the frontend shows on each badge.
BADGE_TEXT: dict[Provenance, str] = {
    Provenance.VERIFIED: "From official catalog",
    Provenance.STUDENT_CONFIRMED: "Confirmed by you",
    Provenance.AI_SUGGESTED: "AI suggestion — verify with your advisor",
    Provenance.UNVERIFIED_EXTRACTION: "Not yet confirmed",
}


class Sourced[T](BaseModel):
    """A value plus where it came from.

    Wrap anything the user will see whose trustworthiness matters:

        Sourced[int](value=68, provenance=Provenance.VERIFIED, source="morgan_cosc_bs_2024")
    """

    value: T
    provenance: Provenance
    source: str | None = Field(
        default=None,
        description="Catalog file id, ingestion job id, or model id — whatever produced this.",
    )

    @property
    def trusted_for_audit(self) -> bool:
        return self.provenance in TRUSTED_FOR_AUDIT
