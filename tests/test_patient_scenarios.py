from dataclasses import FrozenInstanceError
import unittest

from call_safety import ASSESSMENT_DESTINATION
from patient_scenarios import build_patient_prompt, get_scenario


class PatientScenarioTests(unittest.TestCase):
    def setUp(self):
        self.scenario = get_scenario("schedule_routine_visit")

    def test_stored_scenario(self):
        self.assertEqual(self.scenario.scenario_id, "schedule_routine_visit")
        self.assertEqual(self.scenario.patient_name, "Jordan Lee")
        self.assertEqual(self.scenario.date_of_birth, "1990-04-12")
        self.assertEqual(self.scenario.goal, "schedule a routine annual physical")
        self.assertEqual(
            self.scenario.known_facts,
            ("Prefers Tuesday or Thursday after 2:00 PM.",),
        )
        self.assertEqual(
            self.scenario.opening_line,
            "Hi, I'd like to schedule an annual physical.",
        )

    def test_scenario_is_frozen(self):
        with self.assertRaises(FrozenInstanceError):
            self.scenario.patient_name = "Changed Name"

    def test_unknown_id_is_rejected(self):
        with self.assertRaises(ValueError):
            get_scenario("fictional_unknown_scenario")

    def test_prompt_contains_scenario_and_required_instructions(self):
        prompt = build_patient_prompt(self.scenario)
        required_text = (
            "acting as a fictional patient",
            "Speak naturally and briefly",
            "Reveal the known facts\ngradually when they are relevant instead of reciting them",
            "Listen to and adapt\nto the healthcare agent's questions",
            "Ask for clarification when needed",
            "Never\ninvent unknown personal or medical details",
            "say that you do not have that information",
            "until the goal is completed or the agent gives a clear barrier",
            "Never claim\nthere is a real emergency or that a real appointment was created",
        )
        for text in required_text:
            with self.subTest(text=text):
                self.assertIn(text, prompt)
        for value in (
            self.scenario.patient_name,
            self.scenario.date_of_birth,
            self.scenario.goal,
            *self.scenario.known_facts,
            self.scenario.opening_line,
        ):
            self.assertIn(value, prompt)

    def test_prompt_excludes_destination_and_fictional_secret_values(self):
        prompt = build_patient_prompt(self.scenario)
        fictional_secret_values = (
            "fictional-openai-api-secret",
            "fictional-twilio-account-secret",
            "fictional-twilio-auth-secret",
        )
        self.assertNotIn(ASSESSMENT_DESTINATION, prompt)
        for value in fictional_secret_values:
            with self.subTest(value=value):
                self.assertNotIn(value, prompt)


if __name__ == "__main__":
    unittest.main()
