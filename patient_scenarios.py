from dataclasses import dataclass


@dataclass(frozen=True)
class PatientScenario:
    scenario_id: str
    patient_name: str
    date_of_birth: str
    goal: str
    known_facts: tuple[str, ...]
    opening_line: str


_SCENARIOS = {
    "schedule_routine_visit": PatientScenario(
        scenario_id="schedule_routine_visit",
        patient_name="Jordan Lee",
        date_of_birth="1990-04-12",
        goal="schedule a routine annual physical",
        known_facts=("Prefers Tuesday or Thursday after 2:00 PM.",),
        opening_line="Hi, I'd like to schedule an annual physical.",
    ),
}


def get_scenario(scenario_id):
    """Return a fictional patient scenario by its exact ID."""
    try:
        return _SCENARIOS[scenario_id]
    except (KeyError, TypeError):
        raise ValueError(f"Unknown patient scenario ID: {scenario_id!r}") from None


def build_patient_prompt(scenario):
    """Build instructions for a future voice model from a patient scenario."""
    facts = "\n".join(f"- {fact}" for fact in scenario.known_facts)
    return f"""You are acting as a fictional patient for an offline test scenario.

Scenario:
- Patient name: {scenario.patient_name}
- Date of birth: {scenario.date_of_birth}
- Goal: {scenario.goal}
- Known facts:
{facts}
- Opening line: {scenario.opening_line}

Begin with the opening line. Speak naturally and briefly. Reveal the known facts
gradually when they are relevant instead of reciting them. Listen to and adapt
to the healthcare agent's questions. Ask for clarification when needed. Never
invent unknown personal or medical details. If the scenario does not provide
requested information, say that you do not have that information. Continue
until the goal is completed or the agent gives a clear barrier. Never claim
there is a real emergency or that a real appointment was created.
"""
