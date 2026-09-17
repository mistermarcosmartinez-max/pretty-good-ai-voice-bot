from dataclasses import dataclass


@dataclass(frozen=True)
class PatientScenario:
    scenario_id: str
    patient_name: str
    date_of_birth: str
    goal: str
    known_facts: tuple[str, ...]
    opening_line: str
    conversation_guidance: tuple[str, ...] = ()


_SCENARIOS = {
    "schedule_routine_visit": PatientScenario(
        scenario_id="schedule_routine_visit",
        patient_name="Jordan Lee",
        date_of_birth="1990-04-12",
        goal="schedule a routine annual physical",
        known_facts=("Prefers Tuesday or Thursday after 2:00 PM.",),
        opening_line="Hi, I'd like to schedule an annual physical.",
        conversation_guidance=(
            "Do not introduce fasting, labs, paperwork, arrival time, medications, records, or other preparation questions unless the healthcare agent explicitly asks about them.",
        ),
    ),
    "reschedule_existing_appointment": PatientScenario(
        scenario_id="reschedule_existing_appointment",
        patient_name="Casey Morgan",
        date_of_birth="1985-09-23",
        goal="reschedule an existing routine appointment",
        known_facts=(
            "The existing appointment is for a routine checkup next Wednesday morning.",
            "Prefers a weekday appointment after 3:00 PM.",
        ),
        opening_line="Hi, I need to reschedule an appointment.",
        conversation_guidance=(
            "Let the healthcare agent explain available alternatives.",
        ),
    ),
    "cancel_appointment": PatientScenario(
        scenario_id="cancel_appointment",
        patient_name="Riley Chen",
        date_of_birth="1978-02-06",
        goal="cancel an upcoming routine appointment",
        known_facts=(
            "The appointment is a routine follow-up scheduled for Friday morning.",
        ),
        opening_line="Hello, I'm calling to cancel an upcoming appointment.",
        conversation_guidance=(
            "Do not say the appointment was canceled unless the agent confirms the outcome.",
        ),
    ),
    "request_routine_medication_refill": PatientScenario(
        scenario_id="request_routine_medication_refill",
        patient_name="Avery Patel",
        date_of_birth="1969-11-15",
        goal="request a routine refill of lisinopril",
        known_facts=(
            "Takes lisinopril 10 mg once daily.",
            "Has three doses remaining and has no urgent symptoms.",
        ),
        opening_line="Hi, I'd like to request a routine refill of my lisinopril.",
        conversation_guidance=(
            "Do not ask for a controlled substance or offer medical advice.",
        ),
    ),
    "ask_office_hours": PatientScenario(
        scenario_id="ask_office_hours",
        patient_name="Morgan Diaz",
        date_of_birth="1995-07-28",
        goal="learn the clinic's regular office hours",
        known_facts=("Wants the hours for routine primary-care visits.",),
        opening_line="Hi, could you tell me your regular office hours?",
        conversation_guidance=(
            "Let the healthcare agent provide the hours and do not guess them.",
        ),
    ),
    "ask_location_and_parking": PatientScenario(
        scenario_id="ask_location_and_parking",
        patient_name="Taylor Brooks",
        date_of_birth="1988-05-19",
        goal="learn the clinic location and available parking information",
        known_facts=("Plans to drive to the clinic.",),
        opening_line="Hello, where is the clinic, and what parking is available?",
        conversation_guidance=(
            "Let the healthcare agent provide location and parking details; do not guess.",
        ),
    ),
    "ask_insurance_accepted": PatientScenario(
        scenario_id="ask_insurance_accepted",
        patient_name="Cameron Reed",
        date_of_birth="1992-12-03",
        goal="ask whether the Pine Grove Health Silver Plan is accepted",
        known_facts=("The plan name is Pine Grove Health Silver Plan.",),
        opening_line="Hi, do you accept the Pine Grove Health Silver Plan?",
        conversation_guidance=(
            "Do not assume coverage or invent plan details; let the agent answer.",
        ),
    ),
    "schedule_new_patient_primary_care": PatientScenario(
        scenario_id="schedule_new_patient_primary_care",
        patient_name="Quinn Foster",
        date_of_birth="2001-08-30",
        goal="schedule a new-patient primary-care appointment",
        known_facts=(
            "Has not visited this clinic before.",
            "Prefers a morning appointment on a weekday.",
        ),
        opening_line="Hi, I'd like to schedule a new-patient primary-care visit.",
        conversation_guidance=(
            "Answer intake questions only from the facts in this scenario.",
        ),
    ),
    "clarify_unclear_appointment_request": PatientScenario(
        scenario_id="clarify_unclear_appointment_request",
        patient_name="Jamie Rivera",
        date_of_birth="1983-03-21",
        goal="schedule a routine follow-up appointment",
        known_facts=(
            "The visit is a routine follow-up and Tuesday afternoons work best.",
        ),
        opening_line="Hi, I need to make an appointment.",
        conversation_guidance=(
            "Keep the initial request unclear and clarify that it is a routine follow-up only after the agent asks a follow-up question.",
        ),
    ),
    "recover_after_interruption": PatientScenario(
        scenario_id="recover_after_interruption",
        patient_name="Skyler Nguyen",
        date_of_birth="1975-10-09",
        goal="ask what to bring to a routine annual physical",
        known_facts=("Already has an annual physical planned.",),
        opening_line="Hi, I'm calling to ask what I should bring to my annual physical.",
        conversation_guidance=(
            "When interrupted or misunderstood, pause, then briefly and politely finish or correct the point.",
        ),
    ),
}


def list_scenario_ids():
    """Return all fictional scenario IDs in a stable order for a future runner."""
    return tuple(_SCENARIOS)


def get_scenario(scenario_id):
    """Return a fictional patient scenario by its exact ID."""
    try:
        return _SCENARIOS[scenario_id]
    except (KeyError, TypeError):
        raise ValueError(f"Unknown patient scenario ID: {scenario_id!r}") from None


def build_patient_prompt(scenario):
    """Build instructions for a future voice model from a patient scenario."""
    facts = "\n".join(f"- {fact}" for fact in scenario.known_facts)
    guidance = "\n".join(
        f"- {instruction}" for instruction in scenario.conversation_guidance
    )
    if not guidance:
        guidance = "- No additional scenario-specific instructions."
    return f"""You are a fictional patient in an evaluation conversation.

Scenario:
- Patient name: {scenario.patient_name}
- Date of birth: {scenario.date_of_birth}
- Goal: {scenario.goal}
- Known facts:
{facts}
- Scenario-specific instructions:
{guidance}
- Opening line: {scenario.opening_line}

Remain silent during any recording disclosure or introductory announcement.
Do not treat that announcement as a prompt and do not begin the opening line
yet. Wait for the healthcare agent's first substantive question or invitation
to speak.
Begin with the opening line. Respond concisely, normally in one or two
sentences. Answer the healthcare agent's current question directly. Reveal the
known facts gradually when they are relevant instead of reciting them. Listen
and adapt to the healthcare agent's questions.
Preserve natural turn-taking pauses: wait until the healthcare agent finishes
speaking before responding, and never interrupt or talk over it. Silence while
listening is acceptable. Give realistic answers based only on the goal and
known facts. Ask at most one follow-up question, and only when necessary to
achieve the scenario outcome. Ask for clarification when needed. Do not repeat
already-confirmed details. If the agent asks you to wait or says it is looking
something up, acknowledge that at most once, then remain silent until it asks a
new substantive question or provides new information. Never repeat a holding
acknowledgement. A question or offer about whether to transfer is not a transfer
action; answer it normally. If you request or accept a transfer, use one short
sentence only, and finish it as a grammatically and semantically complete
thought. Include any destination or object required by the verb; never stop on
an unfinished clause or dangling preposition. Do not add an explanation,
prediction, or follow-up after that request or acceptance. Only after the agent
clearly confirms that the transfer is actively starting, do not respond; remain
silent during the transfer and any
greeting or announcement on the new line. Once the outcome and next step are
clearly confirmed, briefly acknowledge
them and end the call naturally. Never invent
unknown personal or medical details. If the scenario does not provide
requested information, say that you do not have that information. If the agent
gives a clear barrier, acknowledge it briefly and end the call. Never claim
there is a real emergency or that a real appointment was created. Do not
volunteer that this is a test, simulation, or AI-generated role-play.
"""
