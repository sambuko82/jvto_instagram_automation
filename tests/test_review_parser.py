import unittest

from jvto_instagram_automation.review_parser import extract_narrative


class ReviewParserTests(unittest.TestCase):
    def test_extract_narrative_detects_destinations_and_highlight(self) -> None:
        text = 'Absolutely fantastic!!! We did the 3-day, 2-night tour to see Bromo, Kawah Ijen, and the Madakaripura waterfalls. Our guide Fauzi and driver Lily were wonderful.'
        narrative = extract_narrative(text, guest_name='Flore Sabbah')
        self.assertEqual(narrative.guest_name, 'Flore Sabbah')
        self.assertIn('Bromo', narrative.destinations)
        self.assertIn('Ijen', narrative.destinations)
        self.assertEqual(narrative.guide_names[0], 'Fauzi')
        self.assertEqual(narrative.driver_names[0], 'Lily')


if __name__ == '__main__':
    unittest.main()
