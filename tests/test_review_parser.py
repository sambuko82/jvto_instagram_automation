import tempfile
import unittest
from pathlib import Path

from jvto_instagram_automation.models import Narrative, ReviewPayload
from jvto_instagram_automation.review_parser import (
    build_caption,
    extract_narrative,
    get_priority_reviews,
    load_posted_review_ids,
    record_posted_review,
)


class ReviewParserTests(unittest.TestCase):
    def test_extract_narrative_detects_destinations_and_highlight(self) -> None:
        text = 'Absolutely fantastic!!! We did the 3-day, 2-night tour to see Bromo, Kawah Ijen, and the Madakaripura waterfalls. Our guide Fauzi and driver Lily were wonderful.'
        narrative = extract_narrative(text, guest_name='Flore Sabbah')
        self.assertEqual(narrative.guest_name, 'Flore Sabbah')
        self.assertIn('Bromo', narrative.destinations)
        self.assertIn('Ijen', narrative.destinations)
        self.assertEqual(narrative.guide_names[0], 'Fauzi')
        self.assertEqual(narrative.driver_names[0], 'Lily')


class GetPriorityReviewsTests(unittest.TestCase):
    def test_sorts_by_media_count_descending(self) -> None:
        reviews = [
            {'comment': 'one photo', 'reviewMediaItems': [{}]},
            {'comment': 'five photos', 'reviewMediaItems': [{}, {}, {}, {}, {}]},
            {'comment': 'no photos'},
        ]
        ranked = get_priority_reviews(reviews, limit=5)
        self.assertEqual(ranked[0]['comment'], 'five photos')
        self.assertEqual(ranked[-1]['comment'], 'no photos')

    def test_excludes_non_five_star_reviews(self) -> None:
        reviews = [
            {'comment': 'great', 'starRating': 'FIVE'},
            {'comment': 'meh', 'starRating': 'THREE'},
        ]
        ranked = get_priority_reviews(reviews, limit=5)
        self.assertEqual(len(ranked), 1)
        self.assertEqual(ranked[0]['comment'], 'great')

    def test_untagged_rating_is_treated_as_valid(self) -> None:
        reviews = [{'comment': 'no rating field at all'}]
        ranked = get_priority_reviews(reviews, limit=5)
        self.assertEqual(len(ranked), 1)

    def test_respects_limit(self) -> None:
        reviews = [{'comment': f'review {i}', 'reviewMediaItems': [{}] * i} for i in range(10)]
        ranked = get_priority_reviews(reviews, limit=3)
        self.assertEqual(len(ranked), 3)

    def test_excludes_already_posted_reviews(self) -> None:
        reviews = [
            {'reviewId': 'r1', 'comment': 'best', 'reviewMediaItems': [{}, {}, {}]},
            {'reviewId': 'r2', 'comment': 'second best', 'reviewMediaItems': [{}]},
        ]
        ranked = get_priority_reviews(reviews, limit=5, exclude_ids={'r1'})
        self.assertEqual(len(ranked), 1)
        self.assertEqual(ranked[0]['comment'], 'second best')


class PostedHistoryTests(unittest.TestCase):
    def test_record_then_load_round_trips(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'posted_history.json'
            self.assertEqual(load_posted_review_ids(path), set())
            record_posted_review(path, 'abc123')
            self.assertEqual(load_posted_review_ids(path), {'abc123'})
            record_posted_review(path, 'def456')
            self.assertEqual(load_posted_review_ids(path), {'abc123', 'def456'})

    def test_missing_review_id_is_a_no_op(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'posted_history.json'
            record_posted_review(path, None)
            self.assertFalse(path.exists())


class BuildCaptionTests(unittest.TestCase):
    def _payload(self, review_url_kind: str, review_url: str | None) -> ReviewPayload:
        narrative = Narrative(
            guest_name='Ari',
            guest_type='honeymoon',
            destinations=['Bromo', 'Ijen'],
            quote_full='A dreamy honeymoon story.',
            review_url=review_url,
            review_url_kind=review_url_kind,
        )
        return ReviewPayload(original={}, narrative=narrative)

    def test_specific_link_claims_this_exact_review(self) -> None:
        caption = build_caption(self._payload('specific', 'https://maps.google.com/review/123'))
        self.assertIn('this exact review', caption)
        self.assertIn('https://maps.google.com/review/123', caption)

    def test_profile_link_does_not_overclaim(self) -> None:
        caption = build_caption(self._payload('profile', 'https://g.page/r/example/review'))
        self.assertIn('more verified reviews', caption)
        self.assertNotIn('this exact review', caption)

    def test_no_link_omits_credibility_claim_entirely(self) -> None:
        caption = build_caption(self._payload('none', None))
        self.assertNotIn('http', caption)


if __name__ == '__main__':
    unittest.main()
