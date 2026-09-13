from dataclasses import FrozenInstanceError
from datetime import date
import re
import unittest

from call_safety import ASSESSMENT_DESTINATION
from patient_scenarios import build_patient_prompt, get_scenario, list_scenario_ids


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

    def test_spoken_scenarios_use_natural_patient_facts(self):
        insurance = get_scenario("ask_insurance_accepted")
        interruption = get_scenario("recover_after_interruption")
        self.assertEqual(
            insurance.goal,
            "ask whether the Pine Grove Health Silver Plan is accepted",
        )
        self.assertEqual(
            interruption.known_facts,
            ("Already has an annual physical planned.",),
        )

    def test_catalog_has_exactly_ten_unique_stable_ids(self):
        expected_ids = (
            "schedule_routine_visit",
            "reschedule_existing_appointment",
            "cancel_appointment",
            "request_routine_medication_refill",
            "ask_office_hours",
            "ask_location_and_parking",
            "ask_insurance_accepted",
            "schedule_new_patient_primary_care",
            "clarify_unclear_appointment_request",
            "recover_after_interruption",
        )
        self.assertEqual(list_scenario_ids(), expected_ids)
        self.assertEqual(len(list_scenario_ids()), 10)
        self.assertEqual(len(set(list_scenario_ids())), 10)

    def test_every_scenario_has_complete_fictional_data(self):
        names = set()
        dates_of_birth = set()
        for scenario_id in list_scenario_ids():
            with self.subTest(scenario_id=scenario_id):
                scenario = get_scenario(scenario_id)
                self.assertEqual(scenario.scenario_id, scenario_id)
                for value in (
                    scenario.patient_name,
                    scenario.date_of_birth,
                    scenario.goal,
                    scenario.opening_line,
                ):
                    self.assertIsInstance(value, str)
                    self.assertTrue(value.strip())
                self.assertIsInstance(scenario.known_facts, tuple)
                self.assertTrue(scenario.known_facts)
                self.assertTrue(all(fact.strip() for fact in scenario.known_facts))
                self.assertIsInstance(scenario.conversation_guidance, tuple)
                self.assertTrue(
                    all(item.strip() for item in scenario.conversation_guidance)
                )
                date.fromisoformat(scenario.date_of_birth)
                names.add(scenario.patient_name)
                dates_of_birth.add(scenario.date_of_birth)
        self.assertEqual(len(names), 10)
        self.assertEqual(len(dates_of_birth), 10)

    def test_prompt_contains_scenario_and_required_instructions(self):
        prompt = build_patient_prompt(self.scenario)
        required_text = (
            "You are a fictional patient in an evaluation conversation.",
            "Speak naturally and briefly",
            "Reveal the known facts\ngradually when they are relevant instead of reciting them",
            "Listen to and adapt\nto the healthcare agent's questions",
            "Ask for clarification when needed",
            "Never\ninvent unknown personal or medical details",
            "say that you do not have that information",
            "until the goal is completed or the agent gives a clear barrier",
            "Never claim\nthere is a real emergency or that a real appointment was created",
            "Do not\nvolunteer that this is a test, simulation, or AI-generated role-play",
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
        self.assertNotIn("offline test scenario", prompt)

    def test_every_prompt_contains_its_facts_and_guidance(self):
        for scenario_id in list_scenario_ids():
            scenario = get_scenario(scenario_id)
            prompt = build_patient_prompt(scenario)
            expected_values = (
                scenario.patient_name,
                scenario.date_of_birth,
                scenario.goal,
                *scenario.known_facts,
                *scenario.conversation_guidance,
                scenario.opening_line,
            )
            for value in expected_values:
                with self.subTest(scenario_id=scenario_id, value=value):
                    self.assertIn(value, prompt)

    def test_prompts_exclude_destination_secrets_and_phone_numbers(self):
        fictional_secret_values = (
            "fictional-openai-api-secret",
            "fictional-twilio-account-secret",
            "fictional-twilio-auth-secret",
        )
        phone_number_pattern = re.compile(
            r"(?<![\w-])(?:\+?1[ .-]?)?\(?\d{3}\)?[ .-]?\d{3}[ .-]?\d{4}(?!\w)"
        )
        for scenario_id in list_scenario_ids():
            prompt = build_patient_prompt(get_scenario(scenario_id))
            with self.subTest(scenario_id=scenario_id):
                self.assertNotIn(ASSESSMENT_DESTINATION, prompt)
                self.assertIsNone(phone_number_pattern.search(prompt))
                for value in fictional_secret_values:
                    self.assertNotIn(value, prompt)


if __name__ == "__main__":
    unittest.main()
